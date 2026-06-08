# Catalog Domain Parser Lite

Урезанная версия парсера доменов из каталогов организаций.

## Что внутри

- 3 каталога: `orgpage`, `spravker`, `firmap`
- дедупликация доменов
- чекпоинт на случай обрыва
- базовая проверка сайтов: HTTP-статус, финальный URL, title, meta robots, признаки контактов
- без WHOIS и дополнительных SEO API

## Установка

```bash
python3 -m pip install -r requirements_lite.txt
```

## Запуск

```bash
python3 catalog_domain_parser_lite.py
```

Запросы вводятся через `|`, например:

```text
бизнес школа|повышение квалификации|корпоративное обучение
```

## Результаты

Файлы сохраняются в папку `output_lite`:

- `parsed_domains_all.csv` - все найденные записи
- `parsed_domains_unique.csv` - уникальные домены
- `parsed_domains_site_audit.csv` - базовая проверка сайтов
