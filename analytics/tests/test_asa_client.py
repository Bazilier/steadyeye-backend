"""Tests for AsaClient — JWT auth, token caching/refresh, report parsing.

No real network calls: `requests` inside the client module is patched.
"""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.cache import cache
from django.test import TestCase, override_settings

from analytics.services import asa_client as asa_mod
from analytics.services.asa_client import ACCESS_TOKEN_CACHE_KEY, AsaClient


def _generate_ec_key():
    """Generate an EC P-256 key + its PKCS8 PEM, as Apple issues for ASA."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


_KEY_OBJ, _KEY_PEM = _generate_ec_key()


def _resp(status_code, body):
    """Build a fake requests.Response-like object."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    mock.text = json.dumps(body)
    return mock


@override_settings(
    ASA_PRIVATE_KEY=_KEY_PEM,
    ASA_CLIENT_ID='SEARCHADS.client-abc',
    ASA_TEAM_ID='SEARCHADS.team-xyz',
    ASA_KEY_ID='key-id-001',
)
class AsaClientTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_jwt_generation(self):
        token = AsaClient()._generate_jwt()

        header = jwt.get_unverified_header(token)
        self.assertEqual(header['alg'], 'ES256')
        self.assertEqual(header['kid'], 'key-id-001')

        payload = jwt.decode(
            token,
            _KEY_OBJ.public_key(),
            algorithms=['ES256'],
            audience='https://appleid.apple.com',
        )
        self.assertEqual(payload['sub'], 'SEARCHADS.client-abc')
        self.assertEqual(payload['iss'], 'SEARCHADS.team-xyz')
        self.assertGreater(payload['exp'], payload['iat'])

    def test_token_exchange_success(self):
        client = AsaClient()
        with patch.object(asa_mod.requests, 'post') as mock_post:
            mock_post.return_value = _resp(200, {'access_token': 'tok-123', 'expires_in': 3600})
            token = client._get_access_token()

        self.assertEqual(token, 'tok-123')
        self.assertEqual(cache.get(ACCESS_TOKEN_CACHE_KEY), 'tok-123')
        self.assertEqual(mock_post.call_count, 1)

    def test_token_caching(self):
        client = AsaClient()
        with patch.object(asa_mod.requests, 'post') as mock_post:
            mock_post.return_value = _resp(200, {'access_token': 'tok-cached'})
            first = client._get_access_token()
            second = client._get_access_token()

        self.assertEqual(first, second)
        self.assertEqual(mock_post.call_count, 1)  # second call served from cache

    def test_token_refresh_on_401(self):
        client = AsaClient()
        with patch.object(asa_mod.requests, 'post') as mock_post, \
                patch.object(asa_mod.requests, 'request') as mock_request:
            mock_post.return_value = _resp(200, {'access_token': 'tok-fresh'})
            mock_request.side_effect = [
                _resp(401, {'error': 'unauthorized'}),
                _resp(200, {'data': {'ok': True}}),
            ]
            result = client._make_request('GET', '/campaigns')

        self.assertEqual(result, {'data': {'ok': True}})
        self.assertEqual(mock_request.call_count, 2)   # original + retry
        self.assertEqual(mock_post.call_count, 2)      # token minted twice

    def test_campaign_report_parsing(self):
        client = AsaClient()
        report = {
            'data': {
                'reportingDataResponse': {
                    'row': [
                        {
                            'metadata': {'campaignId': 1, 'campaignName': 'Brand US'},
                            'granularity': [
                                {'date': '2026-05-15', 'impressions': 100, 'taps': 12},
                            ],
                        },
                    ]
                }
            }
        }
        with patch.object(asa_mod.requests, 'post') as mock_post, \
                patch.object(asa_mod.requests, 'request') as mock_request:
            mock_post.return_value = _resp(200, {'access_token': 'tok'})
            mock_request.return_value = _resp(200, report)
            rows = client.fetch_campaign_report(dt.date(2026, 5, 1), dt.date(2026, 5, 15))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['metadata']['campaignName'], 'Brand US')
        self.assertEqual(rows[0]['granularity'][0]['impressions'], 100)
