<?php

declare(strict_types=1);

/**
 * Yerel: php -S 127.0.0.1:8099 router.php
 * Statik dosyalari dosya olarak sunar; diger tum yollar index.php on kumandasina gider.
 */
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$path = is_string($path) ? $path : '/';
$full = __DIR__ . $path;
if ($path !== '/' && is_file($full) && !str_ends_with(strtolower($path), '.php')) {
    return false;
}
require __DIR__ . '/index.php';
