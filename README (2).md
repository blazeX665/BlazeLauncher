# 🔥 BlazeLauncher

Лаунчер Minecraft на чистом Python (только стандартная библиотека). Работает на **Windows 7 – 11**.

## Возможности
- Скачивание и запуск любых релизов и снапшотов Minecraft (1.7+)
- Fabric одной галочкой (папка модов: кнопка **Mods folder**)
- Автоматическая загрузка нужной Java (Adoptium) или использование установленной
- Офлайн-ник или вход через Microsoft аккаунт
- Настройка памяти, пути к Java, лог игры

## Запуск из исходников
1. Установи Python 3.8+ (для Windows 7 — именно **3.8**).
2. Запусти `run.bat` (или `python launcher.py`).

## Готовый .exe через GitHub
1. Создай репозиторий и залей все файлы.
2. Вкладка **Actions → Build BlazeLauncher.exe → Run workflow**, скачай артефакт `BlazeLauncher.exe`.
3. Или поставь тег `v1.0.0` — exe появится в **Releases** сам.

Локально: `build.bat` → `dist\BlazeLauncher.exe`.

## Windows 7
- Нужен Service Pack 1 и обновление TLS 1.2 (KB3140245), иначе HTTPS не заработает.
- Java 17/21 на Windows 7 официально не работает → играй на **Minecraft до 1.16.5** (Java 8).
- На Windows 10/11 работают все версии.

## Вход через Microsoft
Нужен свой Azure Client ID: portal.azure.com → App registrations → New (Personal Microsoft accounts,
Allow public client flows = Yes). Для доступа к Minecraft API Mojang требует одобрение приложения
(форма: https://aka.ms/AppRegistrationForMinecraft). Вставь ID на вкладке **Account**.
Без него работает офлайн-режим (без лицензионных серверов).

## Лицензия
MIT. Minecraft принадлежит Mojang/Microsoft; лаунчер не связан с ними.
