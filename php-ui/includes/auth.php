<?php

declare(strict_types=1);

require_once __DIR__ . '/config.php';
require_once __DIR__ . '/http.php';

function app_session_start(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    session_name('agentbase_sid');
    $cookiePath = app_base_path() === '' ? '/' : app_base_path() . '/';
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => $cookiePath,
        'httponly' => true,
        'samesite' => 'Lax',
    ]);
    session_start();
}

/** @return array{id: int, username: string, uid: string}|null */
function app_current_user(): ?array
{
    app_session_start();
    $u = $_SESSION['user'] ?? null;
    if (!is_array($u) || !isset($u['id'], $u['username'])) {
        return null;
    }
    return [
        'id' => (int) $u['id'],
        'username' => (string) $u['username'],
        'uid' => (string) ($u['uid'] ?? ''),
    ];
}

function app_access_token(): ?string
{
    app_session_start();
    $t = $_SESSION['access_token'] ?? null;
    return is_string($t) && $t !== '' ? $t : null;
}

function app_set_session(string $accessToken, array $user): void
{
    app_session_start();
    $_SESSION['access_token'] = $accessToken;
    $_SESSION['user'] = $user;
}

function app_clear_session(): void
{
    app_session_start();
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $p = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], $p['secure'], $p['httponly']);
    }
    session_destroy();
}

function app_require_login(): void
{
    if (app_access_token() === null || app_current_user() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
}

/** Sunucu tarafinda token gecerliligini dogrula (istege bagli). */
function app_refresh_user_from_api(): void
{
    $t = app_access_token();
    if ($t === null) {
        return;
    }
    $r = app_http_json('GET', app_internal_api('/auth/me'), null, $t);
    if (!$r['ok'] || !is_array($r['json'])) {
        app_clear_session();
        return;
    }
    $j = $r['json'];
    app_set_session($t, [
        'id' => (int) ($j['id'] ?? 0),
        'username' => (string) ($j['username'] ?? ''),
        'uid' => (string) ($j['uid'] ?? ''),
    ]);
}
