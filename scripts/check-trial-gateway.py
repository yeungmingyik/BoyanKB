import argparse
import json
from pathlib import Path
import ssl
import urllib.error
import urllib.request


ORIGINS = {'http://127.0.0.1:8090', 'https://kb.boyantech.xyz'}
CASES = [('GET', '/login', 200), ('GET', '/api/v1/auth/config', 200), ('POST', '/api/v1/auth/register', 403), ('GET', '/api/v1/system/admin/list', 404), ('GET', '/api/v1/skills', 404), ('GET', '/api/v1/sandbox-configs', 403)]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', choices=sorted(ORIGINS), default='http://127.0.0.1:8090')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    results = []
    for method, path, expected in CASES:
        request = urllib.request.Request(args.base_url + path, data=b'{}' if method == 'POST' else None, method=method, headers={'Host': 'kb.boyantech.xyz', 'Content-Type': 'application/json', 'User-Agent': 'BoyanKB-DeploymentCheck/1.0'})
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            passed = response.status == expected
            body = response.read(65536)
            if path == '/api/v1/auth/config' and passed:
                config = json.loads(body)
                passed = config.get('registration_mode') == 'invite_only' and config.get('complex_password_enabled') is True
            if path == '/login' and passed:
                passed = b'BoyanKB' in body and response.headers.get('Referrer-Policy') == 'no-referrer'
            results.append({'method': method, 'path': path, 'http_status': response.status, 'passed': passed})
    report = {'origin': args.base_url, 'https_certificate_verified': args.base_url.startswith('https://'), 'passed': all(result['passed'] for result in results), 'checks': results}
    if args.report:
        destination = args.report.resolve()
        root = Path(__file__).resolve().parents[1] / 'temp/enterprise-trial'
        if not destination.is_relative_to(root) or destination.exists():
            raise ValueError('invalid_report_destination')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, urllib.error.URLError):
        print(json.dumps({'passed': False, 'code': 'gateway_check_failed'}))
        raise SystemExit(1)
