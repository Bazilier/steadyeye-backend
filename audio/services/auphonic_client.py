"""Minimal Auphonic API client built on `requests`.

Three calls start a production (create with settings, upload, start); one
reads it back; one deletes it. Docs: https://auphonic.com/help/api/

The processing settings are fixed here, never read from a request. The one
hard rule: the output must stay exactly as long as the input, because the
client lays it back under the original video track. So every cutting
algorithm is off explicitly. Auphonic's own default for `silence_cutter` is
true (it is only inert while `cutter` is false), so leaving keys out is not
enough. The same goes for intro/outro files and chapters: none are sent.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = 'https://auphonic.com/api'
DOWNLOAD_PREFIX = 'https://auphonic.com/'
TIMEOUT_SECONDS = 20
UPLOAD_TIMEOUT_SECONDS = 90

ALGORITHMS = {
    # Adaptive leveler: evens out loud and quiet passages.
    'leveler': True,
    # Loudness normalization. -16 LUFS is Auphonic's "Podcasts and Mobile"
    # target; social platforms turn louder uploads down anyway.
    'normloudness': True,
    'loudnesstarget': -16,
    'maxpeak': -1,
    # Noise and reverb reduction. "dynamic" keeps speech and music and
    # removes everything else; "speech_isolation" would also strip any
    # background music the user recorded on purpose.
    'denoise': True,
    'denoisemethod': 'dynamic',
    'denoiseamount': 0,
    'filtering': True,
    'filtermethod': 'autoeq',
    # Length must not change: no cutting of any kind.
    'cutter': False,
    'silence_cutter': False,
    'filler_cutter': False,
    'cough_cutter': False,
    'music_cutter': False,
    'cut_mode': 'export_uncut_audio',
}

# ALAC in an .m4a: lossless, so no AAC encoder delay or padding that would
# shift the audio against the video by a few tens of milliseconds.
OUTPUT_FILES = [{'format': 'alac', 'ending': 'm4a'}]

STATUS_DONE = 3
STATUS_ERROR = 2
# Incomplete, Production Outdated, Empty Production: final, and not a result.
STATUSES_FAILED = {STATUS_ERROR, 9, 11, 98}


class AuphonicError(Exception):
    """Any failure talking to Auphonic."""


def start_production(fileobj, filename: str, title: str) -> str:
    """Create, upload and start a production. Returns its uuid.

    If the upload or start fails, the half-made production is deleted so it
    doesn't linger in the account.
    """
    created = _call('post', '/productions.json', json={
        'metadata': {'title': title},
        'algorithms': ALGORITHMS,
        'output_files': OUTPUT_FILES,
    })
    production_uuid = (created.get('data') or {}).get('uuid')
    if not production_uuid:
        raise AuphonicError('create: no uuid in response')

    try:
        _call(
            'post', f'/production/{production_uuid}/upload.json',
            files={'input_file': (filename, fileobj, 'audio/mp4')},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        _call('post', f'/production/{production_uuid}/start.json')
    except AuphonicError:
        delete_production(production_uuid)
        raise
    return production_uuid


def get_production(production_uuid: str) -> dict:
    """The production's `data` object: status, length, output_files, ..."""
    body = _call('get', f'/production/{production_uuid}.json')
    data = body.get('data')
    if not isinstance(data, dict):
        raise AuphonicError('get: no data in response')
    return data


def result_download_url(data: dict) -> str | None:
    """Download URL of the ALAC output in a finished production's data."""
    for output in data.get('output_files') or []:
        url = output.get('download_url') or ''
        if output.get('format') == 'alac' and url.startswith(DOWNLOAD_PREFIX):
            return url
    return None


def open_download(url: str) -> requests.Response:
    """Stream the result file. The caller must close the response."""
    if not url.startswith(DOWNLOAD_PREFIX):
        raise AuphonicError('download: unexpected url')
    try:
        # Auphonic may redirect to storage on another host; requests drops
        # the Authorization header on a cross-host redirect, so the key only
        # ever goes to auphonic.com.
        resp = requests.request(
            'get', url, headers=_headers(), stream=True, allow_redirects=True,
            timeout=(10, 60),
        )
    except requests.RequestException as exc:
        raise AuphonicError(f'download: {type(exc).__name__}') from None
    if resp.status_code != 200:
        resp.close()
        raise AuphonicError(f'download: http {resp.status_code}')
    return resp


def delete_production(production_uuid: str) -> bool:
    """Delete a production with its files. Never raises."""
    try:
        _call('delete', f'/production/{production_uuid}.json')
        return True
    except AuphonicError as exc:
        logger.warning("auphonic: delete %s failed: %s", production_uuid, exc)
        return False


def _headers() -> dict:
    return {'Authorization': f'bearer {settings.AUPHONIC_API_KEY}'}


def _call(method: str, path: str, *, timeout: int = TIMEOUT_SECONDS, **kwargs) -> dict:
    try:
        resp = requests.request(
            method, API_BASE + path, headers=_headers(), timeout=timeout, **kwargs
        )
    except requests.RequestException as exc:
        # Log the class name only: the exception can carry request headers.
        raise AuphonicError(f'{method} {path}: {type(exc).__name__}') from None

    if resp.status_code not in (200, 201, 204):
        raise AuphonicError(f'{method} {path}: http {resp.status_code} {resp.text[:200]}')
    if resp.status_code == 204 or not resp.text:
        return {}
    try:
        return resp.json()
    except ValueError:
        raise AuphonicError(f'{method} {path}: invalid json') from None
