<?php

declare(strict_types=1);

function app_config(): array
{
    static $cfg;
    if ($cfg !== null) {
        return $cfg;
    }
    $raw = getenv('APP_BASE_PATH') ?: getenv('VITE_BASE_PATH') ?: '/';
    $raw = trim((string) $raw, " \t\n\r\0\x0B/");
    $basePath = $raw === '' ? '' : '/' . $raw;

    $internal = rtrim((string) (getenv('APP_INTERNAL_API_URL') ?: 'http://127.0.0.1:8000'), '/');
    $browserApi = rtrim(
        (string) (getenv('APP_BROWSER_API_BASE') ?: getenv('VITE_API_URL') ?: '/api'),
        '/',
    );
    if ($browserApi === '') {
        $browserApi = '/api';
    }
    $cfg = [
        'base_path' => $basePath,
        'internal_api' => $internal,
        'browser_api_base' => $browserApi,
    ];
    return $cfg;
}

function app_base_path(): string
{
    return app_config()['base_path'];
}

function app_url(string $path): string
{
    $path = $path === '' ? '/' : ($path[0] === '/' ? $path : '/' . $path);
    $bp = app_base_path();
    if ($bp === '') {
        return $path;
    }
    if ($path === '/') {
        return $bp . '/';
    }
    return $bp . $path;
}

function app_internal_api(string $path): string
{
    $path = $path[0] === '/' ? $path : '/' . $path;
    return app_config()['internal_api'] . $path;
}

function app_browser_api_base(): string
{
    return app_config()['browser_api_base'];
}
