<?php
/**
 * Days-left projection from measured chunk bytes, falling back to bitrate.
 * No database. The API fills disk and input rows, then calls nexrec_storage_forecast().
 */
declare(strict_types=1);

function nexrec_parse_size_bytes(string $raw): int {
    $raw = strtoupper(preg_replace('/\s+/', '', trim($raw)) ?? '');
    if ($raw === '' || $raw === '0') {
        return 0;
    }
    if (!preg_match('/^(\d+)([KMGT])?B?$/', $raw, $m)) {
        return 0;
    }
    $n = (int) $m[1];
    $mul = ['' => 1, 'K' => 1024, 'M' => 1024 ** 2, 'G' => 1024 ** 3, 'T' => 1024 ** 4];
    return $n * $mul[$m[2] ?? ''];
}

function nexrec_parse_bitrate_bps(string $raw): float {
    $raw = trim($raw);
    if ($raw === '' || !preg_match('/^(\d+(?:\.\d+)?)([kKmMgG])?$/', $raw, $m)) {
        return 0.0;
    }
    $n = (float) $m[1];
    $suf = strtolower($m[2] ?? '');
    $mul = ['' => 1.0, 'k' => 1000.0, 'm' => 1000000.0, 'g' => 1000000000.0];
    return $n * $mul[$suf];
}

function nexrec_format_days(float $days): string {
    if ($days < 0.05) {
        return 'under 0.1 days';
    }
    $shown = number_format($days, 1, '.', '');
    $unit = ((float) $shown < 1.05) ? 'day' : 'days';
    return $shown . ' ' . $unit;
}

/**
 * @param array{free_bytes:int,total_bytes:int} $disk
 * @param list<array<string,mixed>> $inputs
 * @param array{max_used_percent:int,warn_points:int,floor_bytes:int,default_video:string,default_audio:string} $policy
 * @return array<string,mixed>
 */
function nexrec_storage_forecast(array $disk, array $inputs, array $policy): array {
    $total = max(0, (int) $disk['total_bytes']);
    $free = max(0, (int) $disk['free_bytes']);
    if ($free > $total && $total > 0) {
        $free = $total;
    }
    $used = $total > 0 ? max(0, $total - $free) : 0;
    $usedPct = $total > 0 ? ($used / $total) * 100.0 : 0.0;
    $maxPct = max(0, (int) $policy['max_used_percent']);
    $warnPoints = max(0, (int) $policy['warn_points']);
    $floor = max(0, (int) $policy['floor_bytes']);
    $defV = (string) ($policy['default_video'] ?? '12M');
    $defA = (string) ($policy['default_audio'] ?? '192k');

    $usableParts = [];
    if ($maxPct > 0 && $total > 0) {
        $usableParts[] = (int) floor($total * ($maxPct / 100.0)) - $used;
    }
    if ($floor > 0) {
        $usableParts[] = $free - $floor;
    }
    if (!$usableParts) {
        $usableParts[] = $free;
    }
    $usable = min($usableParts);

    $rows = [];
    $systemRate = 0.0;
    foreach ($inputs as $inp) {
        $stored = max(0, (int) ($inp['stored_bytes'] ?? 0));
        $span = max(0.0, (float) ($inp['span_seconds'] ?? 0));
        $video = trim((string) ($inp['video_bitrate'] ?? ''));
        $audio = trim((string) ($inp['audio_bitrate'] ?? ''));
        if ($video === '') {
            $video = $defV;
        }
        if ($audio === '') {
            $audio = $defA;
        }
        $target = (nexrec_parse_bitrate_bps($video) + nexrec_parse_bitrate_bps($audio)) / 8.0;
        $measured = ($stored > 0 && $span >= 60.0) ? ($stored / $span) : 0.0;
        $rate = $measured > 0 ? $measured : $target;
        $source = $measured > 0 ? 'measured' : 'bitrate';
        $enabled = !empty($inp['enabled']);
        $daysOnDisk = 0.0;
        if ($measured > 0) {
            $daysOnDisk = $span / 86400.0;
        } elseif ($rate > 0 && $stored > 0) {
            $daysOnDisk = $stored / $rate / 86400.0;
        }
        if ($enabled && $rate > 0) {
            $systemRate += $rate;
        }
        $rows[] = [
            'id' => (string) ($inp['id'] ?? ''),
            'name' => (string) (($inp['name'] ?? '') !== '' ? $inp['name'] : ($inp['id'] ?? '')),
            'enabled' => $enabled,
            'retention_days' => max(0, (int) ($inp['retention_days'] ?? 0)),
            'stored_bytes' => $stored,
            'rate_bps' => $rate,
            'rate_source' => $source,
            'days_on_disk' => $daysOnDisk,
            'days_left' => null,
        ];
    }

    $systemDays = null;
    if ($systemRate > 0) {
        $systemDays = max(0.0, (float) $usable) / $systemRate / 86400.0;
    }
    foreach ($rows as $i => $row) {
        if ($row['enabled'] && $row['rate_bps'] > 0) {
            $rows[$i]['days_left'] = max(0.0, (float) $usable) / $row['rate_bps'] / 86400.0;
        }
    }

    $near = $maxPct > 0 && $total > 0 && $usedPct >= ($maxPct - $warnPoints);
    $atFloor = $floor > 0 && $free <= $floor;
    $warn = $near || $atFloor;

    $short = [];
    if ($systemDays !== null) {
        foreach ($rows as $row) {
            $counts = $row['enabled'] || $row['stored_bytes'] > 0;
            if ($counts && $row['retention_days'] > 0 && $systemDays < $row['retention_days']) {
                $short[] = $row['name'] . ' (' . $row['retention_days'] . ' days)';
            }
        }
    }
    $cuts = $short !== [];

    return [
        'warn' => $warn,
        'cuts_retention' => $cuts,
        'message' => $warn ? nexrec_forecast_message($usedPct, $maxPct, $warnPoints, $floor, $atFloor, $systemDays, $short) : '',
        'used_bytes' => $used,
        'free_bytes' => $free,
        'total_bytes' => $total,
        'used_pct' => round($usedPct, 1),
        'usable_bytes' => max(0, $usable),
        'max_used_percent' => $maxPct,
        'warn_points' => $warnPoints,
        'floor_bytes' => $floor,
        'system_days' => $systemDays,
        'system_rate_bps' => $systemRate,
        'inputs' => $rows,
    ];
}

/**
 * @param list<string> $short
 */
function nexrec_forecast_message(float $usedPct, int $maxPct, int $warnPoints, int $floor, bool $atFloor, ?float $systemDays, array $short): string {
    $pct = number_format($usedPct, 1, '.', '');
    $parts = ['Drive is ' . $pct . '% full.'];
    if ($maxPct > 0) {
        $parts[] = 'Purge starts at ' . $maxPct . '% used'
            . ($warnPoints > 0 ? ' (warning begins ' . $warnPoints . ' points sooner)' : '') . '.';
    }
    if ($atFloor && $floor > 0) {
        $parts[] = 'Free space is at the ' . nexrec_format_bytes($floor) . ' floor.';
    }
    if ($systemDays === null) {
        $parts[] = 'No enabled input has a write rate yet, so days left is not projected.';
    } else {
        $parts[] = 'About ' . nexrec_format_days($systemDays) . ' of recording are left for all inputs together.';
        if ($short) {
            $shown = array_slice($short, 0, 6);
            $extra = count($short) - count($shown);
            $list = implode(', ', $shown);
            if ($extra > 0) {
                $list .= ', and ' . $extra . ' more';
            }
            $parts[] = 'That is before the retention on ' . $list . '. The next cleanup pass deletes the oldest video.';
        } else {
            $parts[] = 'That still covers each input\'s retention.';
        }
    }
    return implode(' ', $parts);
}

function nexrec_format_bytes(int $bytes): string {
    if ($bytes >= 1024 ** 4) {
        return number_format($bytes / (1024 ** 4), 1, '.', '') . ' TiB';
    }
    if ($bytes >= 1024 ** 3) {
        return number_format($bytes / (1024 ** 3), 1, '.', '') . ' GiB';
    }
    if ($bytes >= 1024 ** 2) {
        return number_format($bytes / (1024 ** 2), 1, '.', '') . ' MiB';
    }
    return $bytes . ' B';
}
