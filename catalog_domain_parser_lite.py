import csv
import os
import random
import re
import time
from collections import deque
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


OUTPUT_DIR = 'output_lite'
OUTPUT_ALL = os.path.join(OUTPUT_DIR, 'parsed_domains_all.csv')
OUTPUT_UNIQUE = os.path.join(OUTPUT_DIR, 'parsed_domains_unique.csv')
OUTPUT_SITE_AUDIT = os.path.join(OUTPUT_DIR, 'parsed_domains_site_audit.csv')
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, 'checkpoint.txt')

MAX_RESULT_PAGES_PER_SOURCE = 5
MAX_CARD_PAGES_PER_SOURCE = 200
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
PROXY_FILE = 'proxies.txt'

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
]

BLACKLIST_DOMAINS = {
    'vk.com', 'ok.ru', 'youtube.com', 'youtu.be', 't.me', 'telegram.me', 'wa.me',
    'whatsapp.com', 'viber.com', 'facebook.com', 'instagram.com', 'twitter.com',
    'x.com', 'tiktok.com', 'dzen.ru', 'rutube.ru', 'linkedin.com', 'pinterest.com',
    'github.com', 'apple.com', 'yandex.ru', 'google.ru', 'google.com', 'mail.ru',
    'rambler.ru', 'bing.com', 'gstatic.com', 'googleapis.com', 'googletagmanager.com',
    'google-analytics.com', 'doubleclick.net', 'facebook.net', 'yastatic.net',
    'cloudflare.com', 'cloudfront.net', 'jsdelivr.net', 'jquery.com', 'bootstrapcdn.com',
    'schema.org', 'w3.org', 'jivo.ru', 'jivosite.com', 'livetex.ru', 'calltouch.ru',
    'roistat.com', 'bitrix24.ru', '2gis.ru', 'avito.ru', 'youla.ru', 'wildberries.ru',
    'ozon.ru', 'mos.ru', 'gosuslugi.ru', 'pravo.gov.ru', 'orgpage.ru', 'spravker.ru',
    'firmap.ru', 'yell.ru', 'flamp.ru', 'zoon.ru', 'rusprofile.ru', 'list-org.com',
}

ALLOWED_TLDS = {
    'ru', 'рф', 'su', 'com', 'net', 'org', 'info', 'biz', 'pro', 'online', 'site',
    'school', 'education', 'academy', 'mba', 'io', 'ai', 'me',
}

COMPOUND_TLDS = {'com.ru', 'net.ru', 'org.ru', 'pp.ru', 'msk.ru', 'spb.ru'}
REDIRECT_PARAMS = {'url', 'u', 'to', 'target', 'link', 'href', 'site', 'go', 'redirect', 'redir'}
DOMAIN_LINK_ATTRS = ['href', 'data-href', 'data-url', 'data-link', 'data-site']
STATIC_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.css', '.js', '.ico', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar')
BAD_PATH_PARTS = ['/login', '/register', '/user', '/account', '/help', '/privacy', '/policy', '/terms', '/advert', '/contacts', '/about', '/search', '/reviews', '/map', '/maps', '/photo', '/photos', '/img', '/image', '/static', '/assets', '/css', '/js']

URL_RE = re.compile(r'(https?://[^ <]+|www[.][^ <]+|[a-zA-Zа-яА-ЯёЁ0-9-]+[.](?:ru|рф|xn--p1ai|su|com|net|org|info|biz|pro|online|site|school|education|academy|mba|io|ai|me)(?:/[^ <]*)?)', re.I)
DOMAIN_TLD_RE = re.compile(r'[.](ru|рф|xn--p1ai|su|com|net|org|info|biz|pro|online|site|school|education|academy|mba|io|ai|me)(/|$)', re.I)
CONTACT_RE = re.compile(r'(contacts?|kontakty|kontakt|контакты|связаться|обратная связь)', re.I)


def build_sources(query):
    q = quote_plus(query)
    return [
        {
            'name': 'orgpage',
            'host': 'www.orgpage.ru',
            'url': f'https://www.orgpage.ru/search.html?q={q}&loc=%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D1%8F&forReplies=false',
            'delay': (1.5, 3.0),
        },
        {
            'name': 'spravker',
            'host': 'msk.spravker.ru',
            'url': f'https://msk.spravker.ru/search/?q={q}',
            'delay': (1.0, 2.5),
        },
        {
            'name': 'firmap',
            'host': 'firmap.ru',
            'url': f'https://firmap.ru/search/moskva?text={q}',
            'delay': (1.0, 2.5),
        },
    ]


def load_proxies():
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if not line.startswith(('http://', 'https://', 'socks')):
                        line = 'http://' + line
                    proxies.append(line)
    print(f'[PROXY] Загружено {len(proxies)} прокси' if proxies else '[PROXY] Прокси не найдены')
    return proxies


def get_random_proxy(proxies):
    if not proxies:
        return None
    proxy_url = random.choice(proxies)
    return {'http': proxy_url, 'https': proxy_url}


def make_session():
    session = requests.Session()
    rotate_ua(session)
    return session


def rotate_ua(session):
    session.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.7',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
    })


def sleep_random(delay):
    delay = delay or (1.0, 2.5)
    time.sleep(random.uniform(delay[0], delay[1]))


def fetch(session, url, delay=None, proxies=None):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            rotate_ua(session)
            sleep_random(delay)
            kwargs = {'timeout': REQUEST_TIMEOUT, 'allow_redirects': True}
            proxy = get_random_proxy(proxies)
            if proxy:
                kwargs['proxies'] = proxy
            response = session.get(url, **kwargs)
            if response.status_code in (403, 429):
                wait = RETRY_BACKOFF ** attempt + random.uniform(2, 5)
                print(f'[BLOCK] {response.status_code}: {url}, жду {wait:.1f}с')
                time.sleep(wait)
                continue
            if response.status_code >= 500:
                wait = RETRY_BACKOFF ** attempt
                print(f'[SERVER] {response.status_code}: {url}, retry {wait:.1f}с')
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                print(f'[WARN] HTTP {response.status_code}: {url}')
                return None
            if not response.encoding or response.encoding.lower() == 'iso-8859-1':
                response.encoding = response.apparent_encoding
            return response.text
        except requests.exceptions.Timeout:
            wait = RETRY_BACKOFF ** attempt
            print(f'[TIMEOUT] {url}, retry {wait:.1f}с')
            time.sleep(wait)
        except Exception as e:
            print(f'[ERROR] {url} | {e}')
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
    return None


def to_unicode_domain(host):
    host = host.strip().lower().strip('.')
    try:
        return host.encode('ascii').decode('idna')
    except Exception:
        return host


def to_ascii_domain(host):
    host = host.strip().lower().strip('.')
    try:
        return host.encode('idna').decode('ascii')
    except Exception:
        return host


def get_domain_suffix(host):
    parts = host.lower().strip('.').split('.')
    if len(parts) < 2:
        return None
    last_two = '.'.join(parts[-2:])
    return last_two if last_two in COMPOUND_TLDS else parts[-1]


def root_domain(host):
    if not host:
        return None
    host = to_unicode_domain(host.lower().strip().replace('www.', '', 1).split(':')[0].strip('.'))
    parts = [p for p in host.split('.') if p]
    if len(parts) < 2:
        return None
    suffix = get_domain_suffix(host)
    if suffix in COMPOUND_TLDS:
        return '.'.join(parts[-3:]) if len(parts) >= 3 else None
    if suffix in ALLOWED_TLDS:
        return '.'.join(parts[-2:])
    return None


def is_good_domain(domain):
    if not domain:
        return False
    suffix = get_domain_suffix(domain)
    if suffix not in ALLOWED_TLDS and suffix not in COMPOUND_TLDS:
        return False
    if len(domain) < 5:
        return False
    for bad in BLACKLIST_DOMAINS:
        if domain == bad or domain.endswith('.' + bad):
            return False
    return True


def extract_domains_from_url(raw_url):
    found = set()
    if not raw_url:
        return found
    raw_url = unquote(unescape(str(raw_url).strip()))
    if raw_url.startswith('//'):
        raw_url = 'https:' + raw_url
    if raw_url.startswith('www.'):
        raw_url = 'https://' + raw_url
    if not raw_url.startswith(('http://', 'https://')):
        if DOMAIN_TLD_RE.search(raw_url):
            raw_url = 'https://' + raw_url
        else:
            return found
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return found
    domain = root_domain(parsed.netloc)
    if domain and is_good_domain(domain):
        found.add(domain)
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in REDIRECT_PARAMS:
            for value in values:
                found.update(extract_domains_from_url(value))
    return found


def extract_domains_from_html(html):
    found = set()
    if not html:
        return found
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup.find_all(True):
        for attr in DOMAIN_LINK_ATTRS:
            value = tag.get(attr)
            if value:
                found.update(extract_domains_from_url(value))
    for match in URL_RE.findall(soup.get_text(' ')):
        found.update(extract_domains_from_url(match))
    return found


def same_host_or_subdomain(url_host, source_host):
    url_host = url_host.lower().replace('www.', '')
    source_host = source_host.lower().replace('www.', '')
    return url_host == source_host or url_host.endswith('.' + source_host)


def normalize_internal_link(base_url, href):
    if not href:
        return None
    href = href.strip()
    if href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
        return None
    url = urljoin(base_url, href)
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.scheme.startswith('http'):
        return None
    return parsed._replace(fragment='').geturl()


def is_probably_card_url(url, source_host):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    path = parsed.path.lower()
    if not same_host_or_subdomain(parsed.netloc, source_host):
        return False
    if path in {'', '/'} or path.endswith(STATIC_EXTENSIONS):
        return False
    return not any(bad in path for bad in BAD_PATH_PARTS)


def is_probably_pagination_link(tag, url, source_host):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if not same_host_or_subdomain(parsed.netloc, source_host):
        return False
    text = tag.get_text(' ', strip=True).lower()
    href = tag.get('href', '').lower()
    return text in {'следующая', 'дальше', '>', '»', 'next'} or text.isdigit() or any(x in href for x in ['page=', 'p=', 'start=', 'from=', 'offset='])


def extract_links(html, base_url, source_host):
    soup = BeautifulSoup(html, 'lxml')
    card_links = set()
    pagination_links = set()
    for a in soup.find_all('a', href=True):
        url = normalize_internal_link(base_url, a.get('href'))
        if not url:
            continue
        if is_probably_card_url(url, source_host):
            card_links.add(url)
        if is_probably_pagination_link(a, url, source_host):
            pagination_links.add(url)
    return card_links, pagination_links


def parse_source(session, query, source, proxies=None):
    source_name = source['name']
    source_host = source['host']
    delay = source.get('delay')
    print('\n' + '=' * 80)
    print(f'[SOURCE] {source_name}')
    print(f'[QUERY]  {query}')
    print(f'[URL]    {source["url"]}')
    print('=' * 80)

    records = []
    search_pages_seen = set()
    card_pages_seen = set()
    search_queue = deque([source['url']])
    collected_card_links = set()

    while search_queue and len(search_pages_seen) < MAX_RESULT_PAGES_PER_SOURCE:
        search_url = search_queue.popleft()
        if search_url in search_pages_seen:
            continue
        search_pages_seen.add(search_url)
        html = fetch(session, search_url, delay=delay, proxies=proxies)
        if not html:
            continue
        domains = extract_domains_from_html(html)
        for domain in domains:
            records.append({'domain': domain, 'domain_ascii': to_ascii_domain(domain), 'source': source_name, 'query': query, 'found_on': search_url, 'page_type': 'search'})
        card_links, pagination_links = extract_links(html, search_url, source_host)
        collected_card_links.update(card_links)
        for page_url in pagination_links:
            if page_url not in search_pages_seen:
                search_queue.append(page_url)
        print(f'[SEARCH] pages={len(search_pages_seen)} | cards={len(collected_card_links)} | domains={len(domains)}')

    card_links_list = list(collected_card_links)[:MAX_CARD_PAGES_PER_SOURCE]
    print(f'[CARDS] К обходу: {len(card_links_list)}')
    for i, card_url in enumerate(card_links_list, 1):
        if card_url in card_pages_seen:
            continue
        card_pages_seen.add(card_url)
        html = fetch(session, card_url, delay=delay, proxies=proxies)
        if not html:
            continue
        for domain in extract_domains_from_html(html):
            records.append({'domain': domain, 'domain_ascii': to_ascii_domain(domain), 'source': source_name, 'query': query, 'found_on': card_url, 'page_type': 'card'})
        if i % 10 == 0:
            print(f'[CARDS] {i}/{len(card_links_list)} | records={len(records)}')
    print(f'[DONE] {source_name} | records={len(records)}')
    return records


def dedupe_records(records):
    seen = set()
    result = []
    for row in records:
        key = (row['domain'], row['source'], row['query'], row['found_on'], row['page_type'])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def build_unique_domains(records):
    data = {}
    for row in records:
        domain = row['domain']
        if domain not in data:
            data[domain] = {'domain': domain, 'domain_ascii': row['domain_ascii'], 'sources': set(), 'queries': set(), 'examples': []}
        data[domain]['sources'].add(row['source'])
        data[domain]['queries'].add(row['query'])
        if len(data[domain]['examples']) < 5:
            data[domain]['examples'].append(row['found_on'])
    return sorted([
        {'domain': item['domain'], 'domain_ascii': item['domain_ascii'], 'sources': ', '.join(sorted(item['sources'])), 'queries': ', '.join(sorted(item['queries'])), 'examples': ' | '.join(item['examples'])}
        for item in data.values()
    ], key=lambda x: x['domain'])


def save_csv(path, rows, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path, rows, fieldnames):
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def candidate_site_urls(domain_ascii):
    domains = [domain_ascii]
    if not domain_ascii.startswith('www.'):
        domains.append('www.' + domain_ascii)
    for scheme in ('https', 'http'):
        for host in domains:
            yield f'{scheme}://{host}/'


def extract_page_signals(html):
    title = ''
    meta_robots = ''
    indexable = True
    has_contacts = False
    if not html:
        return title, meta_robots, indexable, has_contacts
    soup = BeautifulSoup(html, 'lxml')
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(' ', strip=True)[:250]
    robots_tag = soup.find('meta', attrs={'name': re.compile('robots', re.I)})
    if robots_tag:
        meta_robots = robots_tag.get('content', '').strip()
        indexable = 'noindex' not in meta_robots.lower()
    for a in soup.find_all('a', href=True):
        if CONTACT_RE.search(a.get('href', '')) or CONTACT_RE.search(a.get_text(' ', strip=True)):
            has_contacts = True
            break
    if not has_contacts:
        has_contacts = bool(CONTACT_RE.search(soup.get_text(' ', strip=True)[:5000]))
    return title, meta_robots, indexable, has_contacts


def check_site(session, domain, proxies=None):
    domain_ascii = to_ascii_domain(domain)
    result = {'domain_unicode': domain, 'domain': domain_ascii, 'alive': False, 'http_status': '', 'final_url': '', 'title': '', 'meta_robots': '', 'indexable': False, 'has_contacts': False, 'error': ''}
    last_error = ''
    for url in candidate_site_urls(domain_ascii):
        try:
            rotate_ua(session)
            sleep_random((0.5, 1.5))
            kwargs = {'timeout': REQUEST_TIMEOUT, 'allow_redirects': True}
            proxy = get_random_proxy(proxies)
            if proxy:
                kwargs['proxies'] = proxy
            response = session.get(url, **kwargs)
            result['http_status'] = response.status_code
            result['final_url'] = response.url
            if response.status_code >= 500:
                continue
            if response.status_code < 400:
                if not response.encoding or response.encoding.lower() == 'iso-8859-1':
                    response.encoding = response.apparent_encoding
                if 'html' in response.headers.get('Content-Type', '').lower():
                    title, meta_robots, indexable, has_contacts = extract_page_signals(response.text)
                    result.update({'title': title, 'meta_robots': meta_robots, 'indexable': indexable, 'has_contacts': has_contacts})
                else:
                    result['indexable'] = True
                result['alive'] = True
                return result
        except Exception as e:
            last_error = str(e)
    result['error'] = last_error
    return result


def batch_site_audit(session, domains, proxies=None):
    results = []
    total = len(domains)
    print(f'\n[SITE] Проверяю {total} доменов...')
    for i, domain in enumerate(domains, 1):
        row = check_site(session, domain, proxies=proxies)
        results.append(row)
        if i % 10 == 0 or i == total:
            status = 'OK' if row['alive'] else 'FAIL'
            print(f'[SITE] {i}/{total} | {domain} | {status} | {row["http_status"]}')
    return results


def save_checkpoint(query, source_name):
    with open(CHECKPOINT_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{query}|{source_name}\n')


def load_checkpoint():
    done = set()
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)
    return done


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print('=' * 80)
    print('Парсер доменов из каталогов Lite')
    print('3 каталога: orgpage, spravker, firmap')
    print('=' * 80)
    raw = input('Введите запросы через |: ').strip()
    if not raw:
        print('Пустой запрос. Завершение.')
        return
    queries = [q.strip() for q in raw.split('|') if q.strip()]
    do_site_audit = input('Проверять доступность сайтов? (y/n, по умолчанию y): ').strip().lower() not in ('n', 'no', 'нет', 'н')
    proxies = load_proxies()
    done_keys = load_checkpoint()
    session = make_session()
    all_records = []

    if not done_keys and os.path.exists(OUTPUT_ALL):
        os.remove(OUTPUT_ALL)

    for query in queries:
        for source in build_sources(query):
            checkpoint_key = f'{query}|{source["name"]}'
            if checkpoint_key in done_keys:
                print(f'[SKIP] {checkpoint_key}')
                continue
            records = parse_source(session, query, source, proxies=proxies)
            if records:
                records = dedupe_records(records)
                append_csv(OUTPUT_ALL, records, ['domain', 'domain_ascii', 'source', 'query', 'found_on', 'page_type'])
                all_records.extend(records)
            save_checkpoint(query, source['name'])

    if done_keys and os.path.exists(OUTPUT_ALL):
        with open(OUTPUT_ALL, 'r', encoding='utf-8-sig') as f:
            all_records = list(csv.DictReader(f, delimiter=';'))

    all_records = dedupe_records(all_records)
    unique = build_unique_domains(all_records)
    save_csv(OUTPUT_ALL, all_records, ['domain', 'domain_ascii', 'source', 'query', 'found_on', 'page_type'])
    save_csv(OUTPUT_UNIQUE, unique, ['domain', 'domain_ascii', 'sources', 'queries', 'examples'])
    print(f'Всего записей: {len(all_records)}')
    print(f'Уникальных доменов: {len(unique)}')
    print(f'Файл: {OUTPUT_UNIQUE}')

    if do_site_audit and unique:
        site_results = batch_site_audit(session, [d['domain'] for d in unique], proxies=proxies)
        save_csv(OUTPUT_SITE_AUDIT, site_results, ['domain_unicode', 'domain', 'alive', 'http_status', 'final_url', 'title', 'meta_robots', 'indexable', 'has_contacts', 'error'])
        print(f'Аудит сайтов: {OUTPUT_SITE_AUDIT}')

    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


if __name__ == '__main__':
    main()
