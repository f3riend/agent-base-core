<?php

declare(strict_types=1);

require __DIR__ . '/../includes/bootstrap.php';

function app_request_scheme(): string
{
    $https = strtolower((string) ($_SERVER['HTTPS'] ?? ''));
    if ($https === 'on' || $https === '1') {
        return 'https';
    }
    $forwardedProto = strtolower(trim((string) ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '')));
    if ($forwardedProto !== '') {
        $parts = array_map('trim', explode(',', $forwardedProto));
        if (!empty($parts[0]) && ($parts[0] === 'http' || $parts[0] === 'https')) {
            return $parts[0];
        }
    }
    return 'http';
}

function app_enforce_canonical_host_redirect(): void
{
    $canonical = trim((string) (getenv('APP_CANONICAL_URL') ?: ''));
    if ($canonical === '') {
        return;
    }
    $canonicalParts = parse_url($canonical);
    if (!is_array($canonicalParts) || empty($canonicalParts['host']) || empty($canonicalParts['scheme'])) {
        return;
    }
    $currentHost = strtolower((string) ($_SERVER['HTTP_HOST'] ?? ''));
    if ($currentHost === '') {
        return;
    }
    $currentScheme = app_request_scheme();
    $canonicalHost = strtolower((string) $canonicalParts['host']);
    $canonicalScheme = strtolower((string) $canonicalParts['scheme']);
    $canonicalPort = isset($canonicalParts['port']) ? (int) $canonicalParts['port'] : null;
    $currentPort = isset($_SERVER['SERVER_PORT']) ? (int) $_SERVER['SERVER_PORT'] : null;
    $needRedirect = $currentHost !== $canonicalHost || $currentScheme !== $canonicalScheme;
    if (!$needRedirect && $canonicalPort !== null && $currentPort !== null && $canonicalPort !== $currentPort) {
        $needRedirect = true;
    }
    if (!$needRedirect) {
        return;
    }
    $uri = (string) ($_SERVER['REQUEST_URI'] ?? '/');
    if ($uri === '') {
        $uri = '/';
    }
    $targetBase = rtrim($canonical, '/');
    $target = $targetBase . $uri;
    header('Location: ' . $target, true, 308);
    exit;
}

app_enforce_canonical_host_redirect();

function app_request_path(): string
{
    $uri = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
    $uri = is_string($uri) ? $uri : '/';
    $bp = app_base_path();
    if ($bp !== '' && str_starts_with($uri, $bp)) {
        $uri = substr($uri, strlen($bp)) ?: '/';
    }
    if ($uri === '' || $uri[0] !== '/') {
        $uri = '/' . $uri;
    }
    return $uri;
}

function app_render_layout(string $title, string $content, ?string $extraHead = null): void
{
    include __DIR__ . '/../views/layout.php';
}

function app_fastapi_error(?array $json): string
{
    if (!is_array($json)) {
        return 'Istek basarisiz.';
    }
    if (isset($json['detail'])) {
        $d = $json['detail'];
        if (is_string($d)) {
            return $d;
        }
        if (is_array($d)) {
            $parts = [];
            foreach ($d as $item) {
                if (is_array($item) && isset($item['msg'])) {
                    $parts[] = (string) $item['msg'];
                }
            }
            if ($parts) {
                return implode(' ', $parts);
            }
        }
    }
    if (isset($json['error']) && is_string($json['error'])) {
        return $json['error'];
    }
    return 'Istek basarisiz.';
}

function app_settings_flash_set(string $type, string $message): void
{
    app_session_start();
    $_SESSION['settings_flash'] = ['type' => $type, 'message' => $message];
}

/** @return array{type: string, message: string}|null */
function app_settings_flash_pull(): ?array
{
    app_session_start();
    $v = $_SESSION['settings_flash'] ?? null;
    unset($_SESSION['settings_flash']);
    if (!is_array($v)) {
        return null;
    }
    $type = isset($v['type']) && is_string($v['type']) ? $v['type'] : 'info';
    $message = isset($v['message']) && is_string($v['message']) ? $v['message'] : '';
    if ($message === '') {
        return null;
    }
    return ['type' => $type, 'message' => $message];
}

function app_handle_login_post(): void
{
    $username = strtolower(trim((string) ($_POST['username'] ?? '')));
    $password = (string) ($_POST['password'] ?? '');
    $err = null;
    if ($username === '' || strlen($password) < 6) {
        $err = 'Kullanici adi ve sifre gerekli.';
    } else {
        $r = app_http_json('POST', app_internal_api('/auth/login'), [
            'username' => $username,
            'password' => $password,
        ]);
        if (!$r['ok'] || !is_array($r['json'])) {
            $err = app_fastapi_error(is_array($r['json']) ? $r['json'] : null) ?: mb_substr($r['raw'], 0, 200);
        } else {
            $j = $r['json'];
            $tok = (string) ($j['access_token'] ?? '');
            $u = $j['user'] ?? null;
            if ($tok === '' || !is_array($u)) {
                $err = 'Sunucu yaniti beklenmedik.';
            } else {
                app_session_start();
                app_set_session($tok, [
                    'id' => (int) ($u['id'] ?? 0),
                    'username' => (string) ($u['username'] ?? ''),
                    'uid' => (string) ($u['uid'] ?? ''),
                ]);
                header('Location: ' . app_url('/social-media'), true, 302);
                exit;
            }
        }
    }
    $title = 'Giris Yap';
    ob_start();
    include __DIR__ . '/../views/login.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

function app_handle_register_post(): void
{
    $username = strtolower(trim((string) ($_POST['username'] ?? '')));
    $password = (string) ($_POST['password'] ?? '');
    $confirm = (string) ($_POST['confirm'] ?? '');
    $err = null;
    if ($password !== $confirm) {
        $err = 'Sifreler eslesmiyor.';
    } elseif (strlen($username) < 3 || strlen($password) < 6) {
        $err = 'Kullanici adi (min 3) ve sifre (min 6) gerekli.';
    } else {
        $r = app_http_json('POST', app_internal_api('/auth/register'), [
            'username' => $username,
            'password' => $password,
        ]);
        if (!$r['ok'] || !is_array($r['json'])) {
            $err = app_fastapi_error(is_array($r['json']) ? $r['json'] : null) ?: mb_substr($r['raw'], 0, 200);
        } else {
            $j = $r['json'];
            $tok = (string) ($j['access_token'] ?? '');
            $u = $j['user'] ?? null;
            if ($tok === '' || !is_array($u)) {
                $err = 'Sunucu yaniti beklenmedik.';
            } else {
                app_session_start();
                app_set_session($tok, [
                    'id' => (int) ($u['id'] ?? 0),
                    'username' => (string) ($u['username'] ?? ''),
                    'uid' => (string) ($u['uid'] ?? ''),
                ]);
                header('Location: ' . app_url('/social-media'), true, 302);
                exit;
            }
        }
    }
    $title = 'Kayit Ol';
    ob_start();
    include __DIR__ . '/../views/register.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path = app_request_path();

if ($method === 'POST' && $path === '/logout') {
    app_session_start();
    app_clear_session();
    header('Location: ' . app_url('/login'), true, 302);
    exit;
}

if ($method === 'POST' && $path === '/login') {
    app_handle_login_post();
}

if ($method === 'POST' && $path === '/register') {
    app_handle_register_post();
}

if ($method === 'POST' && str_starts_with($path, '/settings')) {
    app_session_start();
    app_require_login();
    $action = trim((string) ($_POST['settings_action'] ?? ''));
    if ($action === 'update_credentials') {
        $token = app_access_token();
        $currentPassword = (string) ($_POST['current_password'] ?? '');
        $newUsername = trim((string) ($_POST['new_username'] ?? ''));
        $newPassword = (string) ($_POST['new_password'] ?? '');
        $confirmPassword = (string) ($_POST['new_password_confirm'] ?? '');

        if ($token === null) {
            app_settings_flash_set('error', 'Oturum bulunamadi, tekrar giris yapin.');
        } elseif ($currentPassword === '') {
            app_settings_flash_set('error', 'Mevcut sifre gerekli.');
        } elseif ($newPassword !== '' && $newPassword !== $confirmPassword) {
            app_settings_flash_set('error', 'Yeni sifre ve tekrar alani eslesmiyor.');
        } elseif ($newUsername === '' && $newPassword === '') {
            app_settings_flash_set('error', 'Kullanici adi veya sifreden en az birini degistirmelisiniz.');
        } else {
            $payload = ['current_password' => $currentPassword];
            if ($newUsername !== '') {
                $payload['username'] = strtolower($newUsername);
            }
            if ($newPassword !== '') {
                $payload['new_password'] = $newPassword;
            }
            $r = app_http_json('POST', app_internal_api('/auth/update-credentials'), $payload, $token);
            if (!$r['ok'] || !is_array($r['json'])) {
                app_settings_flash_set('error', app_fastapi_error(is_array($r['json']) ? $r['json'] : null));
            } else {
                $j = $r['json'];
                $u = app_current_user();
                app_set_session($token, [
                    'id' => (int) ($j['id'] ?? ($u['id'] ?? 0)),
                    'username' => (string) ($j['username'] ?? ($u['username'] ?? '')),
                    'uid' => (string) ($j['uid'] ?? ($u['uid'] ?? '')),
                ]);
                app_settings_flash_set('success', 'Hesap bilgileri guncellendi.');
            }
        }
        header('Location: ' . app_url('/settings/account'), true, 302);
        exit;
    }

    $loc = (string) ($_POST['ui_locale'] ?? '');
    if ($loc === 'tr' || $loc === 'en') {
        app_set_ui_locale($loc);
    }
    header('Location: ' . app_url($path), true, 302);
    exit;
}

if (str_starts_with($path, '/assets/')) {
    http_response_code(404);
    echo 'Not found';
    exit;
}

if ($path === '/verify-email') {
    header('Location: ' . app_url('/social-media'), true, 302);
    exit;
}

if ($path === '/login') {
    if (app_access_token()) {
        header('Location: ' . app_url('/social-media'), true, 302);
        exit;
    }
    $title = 'Giris Yap';
    $err = null;
    ob_start();
    include __DIR__ . '/../views/login.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

if ($path === '/register') {
    if (app_access_token()) {
        header('Location: ' . app_url('/social-media'), true, 302);
        exit;
    }
    $title = 'Kayit Ol';
    $err = null;
    ob_start();
    include __DIR__ . '/../views/register.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

if ($path === '/forgot-password') {
    $title = 'Sifremi unuttum';
    ob_start();
    include __DIR__ . '/../views/forgot_password.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

if ($path === '/reset-password') {
    $title = 'Sifre yenileme';
    ob_start();
    include __DIR__ . '/../views/reset_password.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

if ($path === '/reset-code') {
    $title = 'E-postani kontrol et';
    ob_start();
    include __DIR__ . '/../views/reset_code.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}

if ($path === '/' || $path === '') {
    if (app_access_token()) {
        header('Location: ' . app_url('/social-media'), true, 302);
        exit;
    }
    header('Location: ' . app_url('/login'), true, 302);
    exit;
}

app_session_start();
if ($path === '/social-media' || $path === '/social-media/system-admin') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    if ($path === '/social-media/system-admin') {
        $title = 'Sistem Yöneticisi';
        ob_start();
        include __DIR__ . '/../views/system_admin.php';
        $content = ob_get_clean();
        $extraHead =
            '<script defer src="' .
            htmlspecialchars(app_url('/assets/js/timeline-store-automation.js'), ENT_QUOTES, 'UTF-8') .
            '"></script>';
        app_render_layout($title, $content, $extraHead);
        exit;
    }
    $title = t('socialMediaMenu');
    ob_start();
    include __DIR__ . '/../views/social_media.php';
    $content = ob_get_clean();
    $extraHead = '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/social-media-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/onay-bekleyenler') {
    header('Location: ' . app_url('/social-media/onay-bekleyenler'), true, 302);
    exit;
}

if ($path === '/social-media/etiketler') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Sosyal Medya Etiketleri';
    ob_start();
    include __DIR__ . '/../views/sm_tags.php';
    $content = ob_get_clean();
    $extraHead =
        '<link rel="stylesheet" href="' . htmlspecialchars(app_url('/assets/css/sm-tags-ui.css'), ENT_QUOTES, 'UTF-8') . '">' .
        '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/sm-tags-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/social-media/sablonlar') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Sosyal Medya Şablonları';
    ob_start();
    include __DIR__ . '/../views/sm_templates.php';
    $content = ob_get_clean();
    $extraHead = '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/sm-templates-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/social-media/onay-bekleyenler') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Sosyal Medya Onay Bekleyenler';
    $approvalsHeading = 'Sosyal Medya Onay Bekleyenler';
    $approvalsIntro = 'Onay bekleyen paylaşımlar ve henüz planlanmamış taslaklar. ';
    ob_start();
    include __DIR__ . '/../views/approvals.php';
    $content = ob_get_clean();
    $boot = json_encode(['mode' => 'social'], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    $extraHead =
        '<script>window.__SM_EMBED_MODE__="approvals";window.__APPROVALS_PAGE__=' . $boot . ';</script>' .
        '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/social-media-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>' .
        '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/approvals-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/triggers') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Tetikleyiciler';
    ob_start();
    include __DIR__ . '/../views/triggers.php';
    $content = ob_get_clean();
    app_render_layout($title, $content);
    exit;
}

if ($path === '/stores') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Mağazalar';
    ob_start();
    include __DIR__ . '/../views/stores.php';
    $content = ob_get_clean();
    app_render_layout($title, $content);
    exit;
}

if ($path === '/campaign-management') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Kampanya Yonetimi';
    ob_start();
    include __DIR__ . '/../views/social_media.php';
    $content = ob_get_clean();
    $extraHead = '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/social-media-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/campaign-management/sablonlar') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Kampanya Şablonları';
    ob_start();
    include __DIR__ . '/../views/sm_templates.php';
    $content = ob_get_clean();
    $extraHead = '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/sm-templates-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/campaign-management/onay-bekleyenler') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Kampanya Onay Bekleyenler';
    $approvalsHeading = 'Kampanya Onay Bekleyenler';
    $approvalsIntro = 'Onay bekleyen kampanya banner planları. ';
    ob_start();
    include __DIR__ . '/../views/approvals.php';
    $content = ob_get_clean();
    $boot = json_encode(['mode' => 'campaign'], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    $extraHead =
        '<script>window.__SM_EMBED_MODE__="approvals";window.__APPROVALS_PAGE__=' . $boot . ';</script>' .
        '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/social-media-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>' .
        '<script type="module" src="' . htmlspecialchars(app_url('/assets/js/approvals-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/settings' || str_starts_with($path, '/settings/')) {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $settingsSection = 'account';
    $allowedSettingsSections = ['account', 'workspace', 'ai', 'api-keys', 'automation', 'security'];
    if (str_starts_with($path, '/settings/')) {
        $candidate = trim(substr($path, strlen('/settings/')));
        if ($candidate === '' || !in_array($candidate, $allowedSettingsSections, true)) {
            http_response_code(404);
            echo 'Sayfa bulunamadi.';
            exit;
        }
        $settingsSection = $candidate;
    }
    $settingsViewMap = [
        'account' => __DIR__ . '/../views/settings/account.php',
        'workspace' => __DIR__ . '/../views/settings/workspace.php',
        'ai' => __DIR__ . '/../views/settings/ai.php',
        'api-keys' => __DIR__ . '/../views/settings/api_keys.php',
        'automation' => __DIR__ . '/../views/settings/automation.php',
        'security' => __DIR__ . '/../views/settings/security.php',
    ];
    $settingsView = $settingsViewMap[$settingsSection] ?? null;
    if ($settingsView === null || !is_file($settingsView)) {
        http_response_code(404);
        echo 'Sayfa bulunamadi.';
        exit;
    }
    $title = t('settingsTitle');
    $settingsFlash = app_settings_flash_pull();
    ob_start();
    include $settingsView;
    $content = ob_get_clean();
    $extraHead = '<script defer src="' . htmlspecialchars(app_url('/assets/js/settings-app.js'), ENT_QUOTES, 'UTF-8') . '"></script>';
    app_render_layout($title, $content, $extraHead);
    exit;
}

if ($path === '/kurallar' || $path === '/rules') {
    // Tur 5: Kurallar sekmesi kaldırıldı; her timeline alt-sekmesinde
    // contextual rule paneli var. İlk açılışta Tümü'ye yönlendir.
    header('Location: ' . app_url('/page/timeline/all'), true, 302);
    exit;
}

if (str_starts_with($path, '/page/')) {
    app_require_login();
    app_refresh_user_from_api();
    $pageId = rawurldecode(substr($path, strlen('/page/')));
    $title = 'Sayfa';
    $timelineSlug = null;
    if (str_starts_with($pageId, 'timeline/')) {
        $timelineSlug = trim(substr($pageId, strlen('timeline/')), '/');
    }
    $extraHead = null;
    if ($timelineSlug !== null && $timelineSlug !== '') {
        $extraHead =
            '<link rel="stylesheet" href="' .
            htmlspecialchars(app_url('/assets/css/timeline-rules.css'), ENT_QUOTES, 'UTF-8') .
            '">' .
            '<script type="module" src="' .
            htmlspecialchars(app_url('/assets/js/timeline-page-rules.js'), ENT_QUOTES, 'UTF-8') .
            '"></script>';
    }
    if ($pageId === 'timeline/store') {
        $title = 'Zaman Tuneli - Magaza Sayfasi';
        $view = __DIR__ . '/../views/timeline/store_page.php';
    } else {
        $view = __DIR__ . '/../views/page.php';
    }
    ob_start();
    include $view;
    $content = ob_get_clean();
    app_render_layout($title, $content, $extraHead);
    exit;
}

http_response_code(404);
echo 'Sayfa bulunamadi.';
