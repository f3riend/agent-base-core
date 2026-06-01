<?php

declare(strict_types=1);

/**
 * @return array{ok: bool, status: int, json: mixed, raw: string}
 */
function app_http_json(string $method, string $absoluteUrl, ?array $jsonBody = null, ?string $bearer = null): array
{
    $ch = curl_init($absoluteUrl);
    $headers = ['Accept: application/json'];
    if ($jsonBody !== null) {
        $headers[] = 'Content-Type: application/json';
        $payload = json_encode($jsonBody, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    } else {
        $payload = null;
    }
    if ($bearer !== null && $bearer !== '') {
        $headers[] = 'Authorization: Bearer ' . $bearer;
    }
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_TIMEOUT => 120,
    ]);
    if ($payload !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
    }
    $raw = (string) curl_exec($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($err !== '') {
        return ['ok' => false, 'status' => 0, 'json' => null, 'raw' => $err];
    }
    try {
        $j = $raw !== '' ? json_decode($raw, true, 512, JSON_THROW_ON_ERROR) : [];
    } catch (Throwable) {
        $j = null;
    }
    return ['ok' => $status >= 200 && $status < 300, 'status' => $status, 'json' => $j, 'raw' => $raw];
}
