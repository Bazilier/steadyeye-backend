"""Shared fakes for the ai app tests. No test here touches the network."""

import json


class FakeResponse:
    """Stand-in for requests.Response — only what our clients actually read."""

    def __init__(self, status_code=200, payload=None, raw_text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = raw_text if raw_text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


def anthropic_ok(text='OPTIMIZED TEXT', input_tokens=11, output_tokens=22):
    return FakeResponse(200, {
        'id': 'msg_test',
        'type': 'message',
        'role': 'assistant',
        'model': 'claude-haiku-4-5-20251001',
        'content': [{'type': 'text', 'text': text}],
        'stop_reason': 'end_turn',
        'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens},
    })


def anthropic_error(status_code=500, error_type='api_error'):
    return FakeResponse(status_code, {'type': 'error', 'error': {
        'type': error_type, 'message': 'something went wrong',
    }})


def rc_paid(expires_date='2099-01-01T00:00:00Z'):
    return FakeResponse(200, {'subscriber': {'entitlements': {
        'access': {'expires_date': expires_date, 'product_identifier': 'annual'},
    }}})


def rc_lifetime():
    """RevenueCat represents a lifetime purchase as a null expires_date."""
    return FakeResponse(200, {'subscriber': {'entitlements': {
        'access': {'expires_date': None, 'product_identifier': 'lifetime'},
    }}})


def rc_free(status_code=200):
    return FakeResponse(status_code, {'subscriber': {'entitlements': {}}})


def rc_created_paid(expires_date='2099-01-01T00:00:00Z'):
    """RevenueCat answers 201 when the GET creates the subscriber record.
    Same body shape as a 200."""
    return FakeResponse(201, {'subscriber': {'entitlements': {
        'access': {'expires_date': expires_date, 'product_identifier': 'annual'},
    }}})


def rc_created_free():
    """201 for a brand-new subscriber that has never purchased."""
    return rc_free(status_code=201)
