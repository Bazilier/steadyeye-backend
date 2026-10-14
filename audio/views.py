"""Server-side Auphonic proxy: "Studio Sound" for a recording's audio track.

The client extracts the audio from a recording, uploads it here, polls until
it's processed, downloads the result and puts it back under the video. The
Auphonic key and every processing setting live only on the server.

    POST   /api/v1/audio/enhance/              multipart `audio` (.m4a), `request_id`
                                               -> 201 {"id", "status": "processing"}
                                               (200 with the same job when this
                                               request_id was already started)
    GET    /api/v1/audio/enhance/<id>/         -> {"id", "status", "length_sec"}
                                                  status: processing | done | error
    GET    /api/v1/audio/enhance/<id>/result/  -> the processed .m4a (ALAC)
    DELETE /api/v1/audio/enhance/<id>/         -> 204; deletes the files at Auphonic

The client is expected to DELETE once it has saved the result, so the user's
audio doesn't stay at Auphonic longer than needed.

Identity is the `X-App-User-Id` header, checked against RevenueCat for the
'access' entitlement — same scheme, and the same caveat, as the ai app: the
header is asserted, not proven, so the quotas are cost ceilings. A job is
only visible to the app_user_id that created it.

Quotas count units (one per started 3 minutes of audio — Auphonic's minimum
charge per production), cheapest check last:
    global per month, global per day   -> 429 rate_limited
    per IP per day                     -> 429 rate_limited
    trial: per whole trial             -> 429 trial_limit
    paid: per calendar month           -> 429 monthly_limit
Failed jobs count nowhere: Auphonic doesn't charge for them.
"""

import logging
import math
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.db.models import Sum
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ai.services.entitlement import is_valid_app_user_id, subscription_status
from ai.views import _client_ip as client_ip

from .models import AudioJob
from .services import auphonic_client as auphonic
from .services.media import m4a_duration_sec

logger = logging.getLogger(__name__)

USER_ID_HEADER = 'X-App-User-Id'

# One recording at a time. 128 kbps mono AAC (what AssetWriterRecorder
# writes) is about 1 MB a minute, so 15 minutes is ~15 MB; the byte cap has
# headroom for files imported from elsewhere.
MAX_INPUT_SEC = 15 * 60
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
# Auphonic bills each production for at least 3 minutes: one quota unit.
UNIT_SEC = 180
# Don't ask Auphonic more often than this per job, however fast the client
# polls. Auphonic asks API users not to hammer the status endpoint.
STATUS_CHECK_INTERVAL = timedelta(seconds=3)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
@parser_classes([MultiPartParser])
def enhance(request):
    app_user_id = request.headers.get(USER_ID_HEADER)
    if not is_valid_app_user_id(app_user_id):
        return _error('invalid_app_user_id', status.HTTP_400_BAD_REQUEST)

    if not settings.AUDIO_ENHANCE_ENABLED or not settings.AUPHONIC_API_KEY:
        return _error('audio_disabled', status.HTTP_503_SERVICE_UNAVAILABLE)

    request_id = request.data.get('request_id') or ''
    if not isinstance(request_id, str) or len(request_id) > 64:
        return _error('invalid_input', status.HTTP_400_BAD_REQUEST)
    if request_id:
        existing = (
            AudioJob.objects
            .filter(app_user_id=app_user_id, request_id=request_id)
            .exclude(status='error')
            .first()
        )
        if existing is not None:
            return Response({'id': str(existing.id), 'status': existing.status})

    upload = request.FILES.get('audio')
    if upload is None:
        return _error('invalid_input', status.HTTP_400_BAD_REQUEST)
    if upload.size > MAX_UPLOAD_BYTES:
        return _error('input_too_large', status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    input_sec = m4a_duration_sec(upload)
    if input_sec is None:
        return _error('invalid_audio', status.HTTP_400_BAD_REQUEST)
    if input_sec > MAX_INPUT_SEC:
        return _error(
            'input_too_long', status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            extra={'max_sec': MAX_INPUT_SEC},
        )

    # Straight from RevenueCat, uncached: a trial gets a smaller allowance,
    # and when RC can't be asked this refuses rather than guesses.
    tier = subscription_status(app_user_id)
    if tier is None:
        return _error('upstream_error', status.HTTP_502_BAD_GATEWAY)
    if tier == 'free':
        return _error('subscription_required', status.HTTP_403_FORBIDDEN)
    is_trial = tier == 'trial'

    units = max(1, math.ceil(input_sec / UNIT_SEC))
    now = timezone.now().astimezone(dt_timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    jobs = AudioJob.objects.exclude(status='error')

    # The hard stops on the Auphonic bill come first.
    global_month = _units(jobs.filter(created_at__gte=month_start))
    global_day = _units(jobs.filter(created_at__gte=day_start))
    if (global_month + units > settings.AUDIO_ENHANCE_GLOBAL_MONTHLY_UNITS
            or global_day + units > settings.AUDIO_ENHANCE_GLOBAL_DAILY_UNITS):
        logger.warning(
            "audio.enhance: global limit reached (month %s, day %s units)",
            global_month, global_day,
        )
        return _error('rate_limited', status.HTTP_429_TOO_MANY_REQUESTS)

    ip = client_ip(request)
    if ip is not None:
        ip_day = _units(jobs.filter(ip=ip, created_at__gte=day_start))
        if ip_day + units > settings.AUDIO_ENHANCE_IP_DAILY_UNITS:
            logger.warning("audio.enhance: ip daily limit reached for %s", ip)
            return _error('rate_limited', status.HTTP_429_TOO_MANY_REQUESTS)

    user_jobs = jobs.filter(app_user_id=app_user_id)
    if is_trial:
        limit = settings.AUDIO_ENHANCE_TRIAL_UNITS
        used = _units(user_jobs.filter(is_trial=True))
        limit_error = 'trial_limit'
    else:
        limit = settings.AUDIO_ENHANCE_PAID_MONTHLY_UNITS
        used = _units(user_jobs.filter(is_trial=False, created_at__gte=month_start))
        limit_error = 'monthly_limit'
    if used + units > limit:
        return _error(
            limit_error, status.HTTP_429_TOO_MANY_REQUESTS,
            extra={'limit': limit, 'used': used, 'units': units},
        )

    job = AudioJob.objects.create(
        app_user_id=app_user_id, request_id=request_id, input_sec=input_sec,
        units=units, is_trial=is_trial, ip=ip,
    )
    try:
        job.production_uuid = auphonic.start_production(
            upload, filename=f'{job.id}.m4a', title=str(job.id)
        )
    except auphonic.AuphonicError as exc:
        logger.warning("audio.enhance upstream_error: %s", exc)
        job.status = 'error'
        job.error_message = 'start_failed'
        job.save(update_fields=['status', 'error_message'])
        return _error('upstream_error', status.HTTP_502_BAD_GATEWAY)

    job.save(update_fields=['production_uuid'])
    logger.info(
        "audio.enhance started: job=%s app_user_id=%s input_sec=%.2f units=%s trial=%s",
        job.id, app_user_id, input_sec, units, is_trial,
    )
    return Response(
        {
            'id': str(job.id),
            'status': job.status,
            'limit': limit,
            'remaining': max(0, limit - used - units),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET', 'DELETE'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def enhance_job(request, job_id):
    job, error = _own_job(request, job_id)
    if error:
        return error

    if request.method == 'DELETE':
        _delete_at_auphonic(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    if job.status == 'processing' and job.deleted_at is None:
        _refresh(job)
    return Response(_job_payload(job))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def enhance_result(request, job_id):
    job, error = _own_job(request, job_id)
    if error:
        return error
    if job.deleted_at is not None:
        return _error('gone', status.HTTP_410_GONE)
    if job.status != 'done' or not job.result_url:
        return _error('not_ready', status.HTTP_409_CONFLICT)

    try:
        upstream = auphonic.open_download(job.result_url)
    except auphonic.AuphonicError as exc:
        logger.warning("audio.result upstream_error job=%s: %s", job.id, exc)
        return _error('upstream_error', status.HTTP_502_BAD_GATEWAY)

    def stream():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    response = StreamingHttpResponse(stream(), content_type='audio/mp4')
    length = upstream.headers.get('Content-Length')
    if length and length.isdigit():
        response['Content-Length'] = length
    response['Content-Disposition'] = f'attachment; filename="{job.id}.m4a"'
    return response


def _own_job(request, job_id):
    """(job, None) when the caller owns it, else (None, error response).

    Someone else's job answers 404, same as a missing one.
    """
    app_user_id = request.headers.get(USER_ID_HEADER)
    if not is_valid_app_user_id(app_user_id):
        return None, _error('invalid_app_user_id', status.HTTP_400_BAD_REQUEST)
    job = AudioJob.objects.filter(id=job_id, app_user_id=app_user_id).first()
    if job is None:
        return None, _error('not_found', status.HTTP_404_NOT_FOUND)
    return job, None


def _refresh(job: AudioJob) -> None:
    """Pull the production's state from Auphonic, at most every few seconds.

    A failed check leaves the job processing; the client just polls again.
    """
    now = timezone.now()
    if job.checked_at and now - job.checked_at < STATUS_CHECK_INTERVAL:
        return
    job.checked_at = now

    try:
        data = auphonic.get_production(job.production_uuid)
    except auphonic.AuphonicError as exc:
        logger.warning("audio.status upstream_error job=%s: %s", job.id, exc)
        job.save(update_fields=['checked_at'])
        return

    code = data.get('status')
    if code == auphonic.STATUS_DONE:
        url = auphonic.result_download_url(data)
        length = data.get('length')
        if url and isinstance(length, (int, float)):
            job.status = 'done'
            job.result_url = url
            job.length_sec = float(length)
            # input_sec comes from the MP4 header, which counts AAC priming
            # and padding (~0.05-0.1 s); only a bigger gap means a real cut.
            drift = abs(job.length_sec - job.input_sec)
            if drift > 0.15:
                logger.warning(
                    "audio.status job=%s length drift %.3f s (in %.3f, out %.3f)",
                    job.id, drift, job.input_sec, job.length_sec,
                )
        else:
            job.status = 'error'
            job.error_message = 'done_without_result'
    elif code in auphonic.STATUSES_FAILED:
        job.status = 'error'
        job.error_message = (data.get('error_message') or f'status {code}')[:300]
        logger.warning("audio.status job=%s failed: %s", job.id, job.error_message)

    job.save(update_fields=[
        'checked_at', 'status', 'result_url', 'length_sec', 'error_message',
    ])


def _delete_at_auphonic(job: AudioJob) -> None:
    if job.deleted_at is not None:
        return
    if job.production_uuid and not auphonic.delete_production(job.production_uuid):
        return  # leave deleted_at empty so a later DELETE retries
    job.deleted_at = timezone.now()
    job.save(update_fields=['deleted_at'])


def _units(queryset) -> int:
    return queryset.aggregate(total=Sum('units'))['total'] or 0


def _job_payload(job: AudioJob) -> dict:
    return {
        'id': str(job.id),
        'status': job.status,
        'length_sec': job.length_sec,
    }


def _error(code: str, http_status: int, extra: dict | None = None) -> Response:
    payload = {'error': code}
    if extra:
        payload.update(extra)
    return Response(payload, status=http_status)
