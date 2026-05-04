import logging

from django.conf import settings
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Message, User
from .serializers import InboundMessageSerializer, MessageSerializer
from .telegram import (
    extract_user_uuid_from_replied_text,
    format_inbound_for_telegram,
    send_message,
)

logger = logging.getLogger(__name__)


@api_view(['GET'])
def health(request):
    return Response({'status': 'ok'})


@api_view(['GET', 'POST'])
def messages(request):
    if request.method == 'POST':
        return _create_message(request)
    return _list_messages(request)


def _create_message(request):
    serializer = InboundMessageSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    user_uuid = data['user_uuid']
    text = data['text']
    metadata = data.get('metadata') or {}
    email = data.get('email') or None

    user, _ = User.objects.get_or_create(uuid=user_uuid)
    if email:
        user.email = email
    user.last_seen_metadata = metadata
    user.save()

    message = Message.objects.create(
        user=user,
        text=text,
        direction='inbound',
        metadata=metadata,
    )

    formatted = format_inbound_for_telegram(user_uuid, text, metadata, email or user.email)
    telegram_message_id = send_message(formatted)
    if telegram_message_id is not None:
        message.telegram_message_id = telegram_message_id
        message.save(update_fields=['telegram_message_id'])
    else:
        logger.error("Failed to forward message %s to Telegram", message.id)

    return Response({'status': 'ok', 'message_id': message.id})


def _list_messages(request):
    user_uuid = request.query_params.get('user_uuid')
    if not user_uuid:
        return Response(
            {'error': 'user_uuid query parameter is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(uuid=user_uuid)
    except User.DoesNotExist:
        return Response({'messages': []})

    qs = user.messages.all()

    since_raw = request.query_params.get('since')
    if since_raw:
        since_dt = parse_datetime(since_raw)
        if since_dt is None:
            return Response(
                {'error': 'since must be an ISO 8601 datetime'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(created_at__gt=since_dt)

    return Response({'messages': MessageSerializer(qs, many=True).data})


@api_view(['POST'])
def telegram_webhook(request):
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if not settings.TELEGRAM_WEBHOOK_SECRET or secret != settings.TELEGRAM_WEBHOOK_SECRET:
        logger.warning("Telegram webhook called with invalid secret token")
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    update = request.data or {}
    logger.info("Telegram webhook update: %s", update)

    msg = update.get('message')
    if not msg:
        # We don't process edits, channel posts, etc.
        return Response({'status': 'ignored'})

    chat = msg.get('chat') or {}
    chat_id = chat.get('id')
    if str(chat_id) != str(settings.TELEGRAM_OWNER_CHAT_ID):
        logger.info("Ignoring Telegram message from non-owner chat_id=%s", chat_id)
        return Response({'status': 'ignored'})

    reply_to = msg.get('reply_to_message')
    if not reply_to:
        logger.info("Ignoring non-reply Telegram message from owner")
        return Response({'status': 'ignored'})

    replied_text = reply_to.get('text') or reply_to.get('caption') or ''
    user_uuid = extract_user_uuid_from_replied_text(replied_text)
    if not user_uuid:
        logger.warning("Could not extract user_uuid from replied-to message: %r", replied_text[:200])
        return Response({'status': 'ignored'})

    try:
        user = User.objects.get(uuid=user_uuid)
    except User.DoesNotExist:
        logger.warning("Reply targets unknown user_uuid=%s", user_uuid)
        return Response({'status': 'ignored'})

    reply_text = msg.get('text') or msg.get('caption') or ''
    if not reply_text:
        logger.info("Ignoring empty reply from owner")
        return Response({'status': 'ignored'})

    Message.objects.create(
        user=user,
        text=reply_text,
        direction='outbound',
        telegram_message_id=msg.get('message_id'),
    )
    logger.info("Saved outbound reply to user=%s", user_uuid)

    if user.email:
        # TODO: implement email fallback delivery (deferred to v1.1)
        # send_mail(subject="Reply from SteadyEye", message=reply_text, recipient_list=[user.email], ...)
        logger.info("would send email to %s", user.email)

    return Response({'status': 'ok'})
