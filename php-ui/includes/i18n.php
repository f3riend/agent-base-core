<?php

declare(strict_types=1);

/** @return array{tr: array<string, string>, en: array<string, string>} */
function app_ui_strings_blob(): array
{
    static $blob;
    if ($blob !== null) {
        return $blob;
    }
    $path = __DIR__ . '/../locale/strings.json';
    if (!is_readable($path)) {
        return ['tr' => [], 'en' => []];
    }
    $raw = file_get_contents($path);
    $decoded = json_decode($raw ?: '[]', true);
    $blob = is_array($decoded) ? $decoded : ['tr' => [], 'en' => []];
    if (!isset($blob['tr']) || !is_array($blob['tr'])) {
        $blob['tr'] = [];
    }
    if (!isset($blob['en']) || !is_array($blob['en'])) {
        $blob['en'] = [];
    }
    return $blob;
}

function app_ui_locale(): string
{
    app_session_start();
    $l = $_SESSION['ui_locale'] ?? 'tr';
    return $l === 'en' ? 'en' : 'tr';
}

function app_set_ui_locale(string $locale): void
{
    app_session_start();
    $_SESSION['ui_locale'] = $locale === 'en' ? 'en' : 'tr';
}

/** @param array<string, mixed> $vars */
function t(string $key, array $vars = []): string
{
    $loc = app_ui_locale();
    $dict = app_ui_strings_blob()[$loc] ?? [];
    $s = isset($dict[$key]) ? (string) $dict[$key] : $key;
    foreach ($vars as $k => $v) {
        $s = str_replace('{' . $k . '}', (string) $v, $s);
    }
    return $s;
}
