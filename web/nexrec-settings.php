<?php
/**
 * DB-backed station settings (Setup). Secrets never land in app_settings.
 *
 * Resolution: NEXREC_ENV_OVERRIDES=1 (break-glass) uses the environment;
 * otherwise an app_settings row wins; otherwise the environment; otherwise
 * the catalog default. First boot seeds missing keys from the environment.
 */
declare(strict_types=1);

function nexrec_settings_catalog(): array {
    return [
        'station.display_name' => [
            'section' => 'station', 'label' => 'Display name', 'type' => 'string',
            'env' => 'NEXREC_INSTANCE_NAME', 'default' => 'Studio Recorder 01',
            'help' => 'Hostname shown in the UI and sent when this box registers with NexClip.',
        ],
        'station.instance_id' => [
            'section' => 'station', 'label' => 'Hostname id', 'type' => 'ident',
            'env' => 'NEXREC_INSTANCE_ID', 'default' => 'dcwasof2nexrec01',
            'help' => 'Stable name for this recorder. Not a NexAPP grant key — each host has its own service_id.',
        ],
        'station.public_url' => [
            'section' => 'station', 'label' => 'Public URL', 'type' => 'url',
            'env' => 'NEXREC_PUBLIC_URL', 'default' => '',
            'help' => 'HTTPS origin operators and NexClip use to reach this box.',
        ],
        'station.timezone' => [
            'section' => 'station', 'label' => 'Timezone', 'type' => 'string',
            'env' => 'NEXREC_TIMEZONE', 'default' => 'America/New_York',
            'help' => 'Display timezone. Recording timestamps stay UTC. setup.sh enables NTP (chrony).',
        ],
        'station.ntp_notes' => [
            'section' => 'station', 'label' => 'NTP / timecode notes', 'type' => 'text',
            'default' => '',
            'help' => 'Operator notes (chrony source, house timecode). Not read by FFmpeg.',
        ],
        'database.host' => [
            'section' => 'database', 'label' => 'Postgres host', 'type' => 'string',
            'env' => 'NEXREC_PGHOST', 'default' => '127.0.0.1',
            'help' => 'Local PostgreSQL on this recorder. The process connects with nexrec.env. Edit that file and restart to move the server. Password stays in nexrec.env.',
        ],
        'database.port' => [
            'section' => 'database', 'label' => 'Postgres port', 'type' => 'int',
            'min' => 1, 'max' => 65535, 'env' => 'NEXREC_PGPORT', 'default' => '5432',
            'help' => 'Bootstrap copy of NEXREC_PGPORT.',
        ],
        'database.name' => [
            'section' => 'database', 'label' => 'Database name', 'type' => 'string',
            'env' => 'NEXREC_PGDATABASE', 'default' => 'nexrec',
            'help' => 'Bootstrap copy of NEXREC_PGDATABASE.',
        ],
        'database.user' => [
            'section' => 'database', 'label' => 'Database user', 'type' => 'string',
            'env' => 'NEXREC_PGUSER', 'default' => 'nexrec',
            'help' => 'Bootstrap copy of NEXREC_PGUSER. The password is NEXREC_PGPASSWORD in nexrec.env and is not stored here.',
        ],

        'storage.recordings' => [
            'section' => 'storage', 'label' => 'Recordings path', 'type' => 'path',
            'env' => 'NEXREC_STORAGE_DIR', 'default' => '',
            'help' => 'Native chunks. Blank uses $NEXREC_DATA_DIR/storage. Put this on the media pool.',
        ],
        'storage.exports' => [
            'section' => 'storage', 'label' => 'Exports path', 'type' => 'path',
            'env' => 'NEXREC_EXPORTS_DIR', 'default' => '',
            'help' => 'Finished exports. Blank uses <recordings>/exports.',
        ],
        'storage.scratch' => [
            'section' => 'storage', 'label' => 'Scratch path', 'type' => 'path',
            'env' => 'NEXREC_SCRATCH_DIR', 'default' => '',
            'help' => 'Concat/trim scratch. Blank uses <recordings>/tmp. Prefer NVMe.',
        ],
        'storage.free_space_floor' => [
            'section' => 'storage', 'label' => 'Free-space floor', 'type' => 'size',
            'env' => 'NEXREC_FREE_SPACE_FLOOR', 'default' => '50G',
            'help' => 'Cleanup stops deleting once the recordings filesystem has this much free. Example: 50G, 512M.',
        ],

        'retention.raw_days' => [
            'section' => 'retention', 'label' => 'Raw retention (days)', 'type' => 'int',
            'min' => 1, 'max' => 3650, 'env' => 'NEXREC_NATIVE_RETENTION_DAYS', 'default' => '28',
            'help' => 'Default for new inputs. Per-input override stays on Inputs.',
        ],
        'retention.export_days' => [
            'section' => 'retention', 'label' => 'Export retention (days)', 'type' => 'int',
            'min' => 1, 'max' => 3650, 'env' => 'NEXREC_EXPORT_RETENTION_DAYS', 'default' => '15',
            'help' => 'Unprotected exports. Protected exports are kept.',
        ],

        'ffmpeg.path' => [
            'section' => 'ffmpeg', 'label' => 'ffmpeg path', 'type' => 'path',
            'env' => 'NEXREC_FFMPEG', 'default' => '/usr/local/bin/ffmpeg',
            'help' => 'setup.sh builds FFmpeg 9.0.2 here. Distro /usr/bin/ffmpeg is not the DeckLink build.',
        ],
        'ffmpeg.probe' => [
            'section' => 'ffmpeg', 'label' => 'ffprobe path', 'type' => 'path',
            'env' => 'NEXREC_FFPROBE', 'default' => '/usr/local/bin/ffprobe',
        ],
        'ffmpeg.video_bitrate' => [
            'section' => 'ffmpeg', 'label' => 'Broadcast video bitrate', 'type' => 'bitrate',
            'env' => 'NEXREC_BROADCAST_VIDEO_BITRATE', 'default' => '12M',
            'help' => 'H.264 target when not copying a compressed IP bitstream.',
        ],
        'ffmpeg.audio_bitrate' => [
            'section' => 'ffmpeg', 'label' => 'Broadcast audio bitrate', 'type' => 'bitrate',
            'env' => 'NEXREC_BROADCAST_AUDIO_BITRATE', 'default' => '192k',
        ],
        'ffmpeg.preset' => [
            'section' => 'ffmpeg', 'label' => 'x264 preset', 'type' => 'enum',
            'options' => ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'],
            'env' => 'NEXREC_X264_PRESET', 'default' => 'veryfast',
            'help' => 'Broadcast profile is H.264 High@L4.1, yuv420p, AAC-LC. Preset trades CPU for encode speed.',
        ],
        'ffmpeg.segment_seconds' => [
            'section' => 'ffmpeg', 'label' => 'Segment length (minutes)', 'type' => 'int',
            'min' => 1, 'max' => 86400, 'ui' => 'minutes',
            'env' => 'NEXREC_SEGMENT_SECONDS', 'default' => '300',
            'help' => 'Wall-clock MP4 chunks. Production is 5 minutes. Applied when a record worker starts.',
        ],
        'ffmpeg.gop_frames' => [
            'section' => 'ffmpeg', 'label' => 'GOP (frames)', 'type' => 'int',
            'min' => 1, 'max' => 600, 'env' => 'NEXREC_GOP_FRAMES', 'default' => '60',
            'help' => 'Closed GOP. 60 frames is about 2 seconds at 30 fps.',
        ],
        'ffmpeg.decklink_status_bin' => [
            'section' => 'ffmpeg', 'label' => 'DeckLink status helper', 'type' => 'path',
            'env' => 'NEXREC_DECKLINK_STATUS_BIN', 'default' => '',
            'help' => 'Absolute path to nexrec-decklink-status (no spaces). Blank also checks /usr/local/bin. Build tools/decklink-status against the Blackmagic SDK. JSON per sub-device: input_locked, input_mode, busy.',
        ],

        'preview.enabled' => [
            'section' => 'preview', 'label' => 'Preview enabled', 'type' => 'bool',
            'env' => 'NEXREC_PREVIEW_ENABLED', 'default' => '1',
        ],
        'preview.mediamtx_rtsp' => [
            'section' => 'preview', 'label' => 'MediaMTX RTSP', 'type' => 'url',
            'env' => 'NEXREC_MEDIAMTX_RTSP', 'default' => 'rtsp://127.0.0.1:8554',
            'help' => 'FFmpeg publishes the proxy here. MediaMTX serves WHEP to browsers.',
        ],
        'preview.whep_port' => [
            'section' => 'preview', 'label' => 'WHEP port', 'type' => 'int',
            'min' => 1, 'max' => 65535, 'env' => 'NEXREC_WHEP_PORT', 'default' => '8889',
        ],

        'defaults.live_transcode' => [
            'section' => 'defaults', 'label' => 'Live transcode', 'type' => 'bool', 'default' => '0',
            'help' => 'Applied only when creating an input that does not send the field.',
        ],
        'defaults.copy_native' => [
            'section' => 'defaults', 'label' => 'Copy native bitstream', 'type' => 'bool', 'default' => '1',
        ],
        'defaults.upconvert_1080i' => [
            'section' => 'defaults', 'label' => 'Upconvert 1080i to 1080p', 'type' => 'bool', 'default' => '0',
            'help' => 'The only upconvert the recorder will do.',
        ],
        'defaults.preview_enabled' => [
            'section' => 'defaults', 'label' => 'Preview on new inputs', 'type' => 'bool', 'default' => '1',
        ],
        'defaults.max_inputs' => [
            'section' => 'defaults', 'label' => 'Max inputs', 'type' => 'int',
            'min' => 1, 'max' => 32, 'env' => 'NEXREC_MAX_INPUTS', 'default' => '10',
        ],
        'defaults.feat_scte' => [
            'section' => 'defaults', 'label' => 'SCTE detection', 'type' => 'bool', 'default' => '0',
        ],
        'defaults.feat_av_anomaly' => [
            'section' => 'defaults', 'label' => 'Freeze / black / bars', 'type' => 'bool', 'default' => '0',
        ],
        'defaults.feat_captions' => [
            'section' => 'defaults', 'label' => 'Caption capture', 'type' => 'bool', 'default' => '0',
        ],
        'defaults.feat_transcribe' => [
            'section' => 'defaults', 'label' => 'Transcription', 'type' => 'bool', 'default' => '0',
        ],
        'defaults.feat_nielsen' => [
            'section' => 'defaults', 'label' => 'Nielsen presence check', 'type' => 'bool', 'default' => '0',
            'help' => 'Presence-only until a licensed Nielsen SDK wrapper is configured below.',
        ],
        'defaults.feat_monitors' => [
            'section' => 'defaults', 'label' => 'Live confidence monitors', 'type' => 'bool', 'default' => '0',
            'help' => 'Default for new inputs. Live WFM, vectorscope, VU, and 64-band RTA still default off in each browser and read the WHEP preview only. Does not change the record. 64-channel SDI/AES metering is not on the stereo AAC proxy.',
        ],

        'nexapp.enabled' => [
            'section' => 'nexapp', 'label' => 'NexAPP enabled', 'type' => 'bool',
            'env' => 'NEXREC_NEXAPP_ENABLED', 'default' => '0',
        ],
        'nexapp.mode' => [
            'section' => 'nexapp', 'label' => 'Mode', 'type' => 'enum',
            'options' => ['standalone', 'alias', 'wan'],
            'env' => 'NEXREC_DEPLOY_MODE', 'default' => 'standalone',
            'env_map' => [
                'standalone' => 'standalone',
                'alias' => 'alias',
                'wan' => 'wan',
                'nexapp-wan' => 'wan',
                'nexclip-worker' => 'standalone',
            ],
            'help' => 'standalone: local login. alias: same-host NexAPP cookie. wan: launch ticket redeem. NexClip Mode 2 is separate.',
        ],
        'nexapp.base_url' => [
            'section' => 'nexapp', 'label' => 'Base URL', 'type' => 'url',
            'env' => 'NEXAPP_BASE_URL', 'default' => '',
            'help' => 'Hub origin. Used when issuer is blank.',
        ],
        'nexapp.issuer' => [
            'section' => 'nexapp', 'label' => 'Issuer', 'type' => 'url',
            'env' => 'NEXAPP_ISSUER', 'default' => '',
            'help' => 'JWT iss. Usually the same origin as the base URL.',
        ],
        'nexapp.service_id' => [
            'section' => 'nexapp', 'label' => 'service_id', 'type' => 'ident',
            'env' => 'NEXAPP_SERVICE_ID', 'default' => 'nexclip-recorder-ctl1',
            'help' => 'One unique id per host. Do not share it with another recorder or a NexClip box.',
        ],
        'nexapp.public_key_path' => [
            'section' => 'nexapp', 'label' => 'JWT public key path', 'type' => 'path',
            'env' => 'NEXAPP_PUBLIC_KEY_PATH', 'default' => '/var/www/nexapp/keys/jwt_public.pem',
            'help' => 'PEM file on disk. Paste a key below to write it under the data auth dir. The PEM is not stored in Postgres.',
        ],
        'nexapp.access_url' => [
            'section' => 'nexapp', 'label' => 'Access URL', 'type' => 'url',
            'env' => 'NEXAPP_ACCESS_URL', 'default' => '',
            'help' => 'Blank derives {issuer}/api/access.php.',
        ],
        'nexapp.redeem_url' => [
            'section' => 'nexapp', 'label' => 'WAN redeem URL', 'type' => 'url',
            'env' => 'NEXAPP_REDEEM_URL', 'default' => '',
            'help' => 'Blank derives {issuer}/api/launch/redeem.php.',
        ],
        'nexapp.logout_url' => [
            'section' => 'nexapp', 'label' => 'Logout URL', 'type' => 'url',
            'env' => 'NEXAPP_LOGOUT_URL', 'default' => '',
        ],
        'nexapp.wan_redeem_secret_ref' => [
            'section' => 'nexapp', 'label' => 'WAN redeem secret ref', 'type' => 'secret_ref',
            'default' => 'env:NEXAPP_LAUNCH_SECRET',
            'help' => 'Pointer only. The secret stays in nexrec.env (NEXAPP_LAUNCH_SECRET).',
        ],

        'nexclip.enabled' => [
            'section' => 'nexclip', 'label' => 'Mode 2 client enabled', 'type' => 'bool',
            'env' => 'NEXREC_NEXCLIP_ENABLED', 'default' => '0',
            'help' => 'Export-request poll only. The calendar does not start or stop capture.',
        ],
        'nexclip.api_base' => [
            'section' => 'nexclip', 'label' => 'API base', 'type' => 'url',
            'env' => 'NEXCLIP_BASE_URL', 'default' => '',
        ],
        'nexclip.api_prefix' => [
            'section' => 'nexclip', 'label' => 'API prefix', 'type' => 'string',
            'env' => 'NEXCLIP_API_PREFIX', 'default' => '/api/v1',
        ],
        'nexclip.enrollment_secret_ref' => [
            'section' => 'nexclip', 'label' => 'Enrollment secret ref', 'type' => 'secret_ref',
            'default' => 'env:NEXCLIP_ENROLLMENT_SECRET',
            'help' => 'Pointer only. Set NEXCLIP_ENROLLMENT_SECRET in nexrec.env.',
        ],
        'nexclip.recorder_id' => [
            'section' => 'nexclip', 'label' => 'recorder_id', 'type' => 'string',
            'env' => 'NEXCLIP_RECORDER_ID', 'default' => '',
            'help' => 'Filled after the first successful register. Leave blank until then.',
        ],
        'nexclip.poll_s' => [
            'section' => 'nexclip', 'label' => 'Poll interval (seconds)', 'type' => 'int',
            'min' => 5, 'max' => 3600, 'env' => 'NEXCLIP_POLL_S', 'default' => '60',
            'help' => 'Operator-facing interval. nexrec-nexclip.timer is installed at 60s; change that unit if you need a different cadence.',
        ],
        'nexclip.num_slots' => [
            'section' => 'nexclip', 'label' => 'Mode 2 slots', 'type' => 'int',
            'min' => 1, 'max' => 8, 'env' => 'NEXCLIP_NUM_SLOTS', 'default' => '8',
        ],

        'ldap.enabled' => [
            'section' => 'ldap', 'label' => 'LDAP enabled', 'type' => 'bool',
            'env' => 'NEXREC_LDAP_ENABLED', 'default' => '0',
            'help' => 'Recorder-local / air-gap only. Production Nex* uses local users and NexAPP, not hub LDAP.',
        ],
        'ldap.url' => [
            'section' => 'ldap', 'label' => 'LDAP URL', 'type' => 'url',
            'env' => 'NEXREC_LDAP_URL', 'default' => '',
        ],
        'ldap.bind_dn' => [
            'section' => 'ldap', 'label' => 'Bind DN', 'type' => 'string',
            'env' => 'NEXREC_LDAP_BIND_DN', 'default' => '',
        ],
        'ldap.base_dn' => [
            'section' => 'ldap', 'label' => 'Base DN', 'type' => 'string',
            'env' => 'NEXREC_LDAP_BASE_DN', 'default' => '',
        ],
        'ldap.user_filter' => [
            'section' => 'ldap', 'label' => 'User filter', 'type' => 'string',
            'env' => 'NEXREC_LDAP_USER_FILTER', 'default' => '(uid=%s)',
        ],
        'ldap.email_attr' => [
            'section' => 'ldap', 'label' => 'Email attribute', 'type' => 'string',
            'env' => 'NEXREC_LDAP_EMAIL_ATTR', 'default' => 'mail',
        ],
        'ldap.display_attr' => [
            'section' => 'ldap', 'label' => 'Display attribute', 'type' => 'string',
            'env' => 'NEXREC_LDAP_DISPLAY_ATTR', 'default' => 'cn',
        ],

        'intelligence.transcribe_engine' => [
            'section' => 'intelligence', 'label' => 'Transcribe engine', 'type' => 'enum',
            'options' => ['none', 'whisper.cpp', 'faster-whisper'],
            'env' => 'NEXREC_TRANSCRIBE_ENGINE', 'default' => 'none',
        ],
        'intelligence.transcribe_cmd' => [
            'section' => 'intelligence', 'label' => 'Transcribe command', 'type' => 'command',
            'env' => 'NEXREC_TRANSCRIBE_CMD', 'default' => '',
            'help' => 'Template with {input} and {output}. Empty leaves transcription off.',
        ],
        'intelligence.transcribe_timeout_s' => [
            'section' => 'intelligence', 'label' => 'Transcribe timeout (s)', 'type' => 'int',
            'min' => 5, 'max' => 7200, 'env' => 'NEXREC_TRANSCRIBE_TIMEOUT_S', 'default' => '300',
        ],
        'intelligence.loudness_timeout_s' => [
            'section' => 'intelligence', 'label' => 'Loudness timeout (s)', 'type' => 'int',
            'min' => 5, 'max' => 7200, 'env' => 'NEXREC_LOUDNESS_TIMEOUT_S', 'default' => '180',
        ],
        'intelligence.nielsen_cmd' => [
            'section' => 'intelligence', 'label' => 'Nielsen presence command', 'type' => 'command',
            'env' => 'NEXREC_NIELSEN_PRESENCE_CMD', 'default' => '',
            'help' => 'Optional best-effort presence command with {input}, {output}, and optional {duration}. Blank keeps the builtin stub.',
        ],
    ];
}

function nexrec_settings_sections(): array {
    return [
        'station' => 'Station',
        'database' => 'Database',
        'storage' => 'Storage',
        'retention' => 'Retention',
        'ffmpeg' => 'Recording / FFmpeg',
        'preview' => 'Preview / MediaMTX',
        'defaults' => 'Defaults for new inputs',
        'nexapp' => 'NexAPP',
        'nexclip' => 'NexClip Mode 2',
        'ldap' => 'LDAP (local only)',
        'intelligence' => 'Monitoring engines',
    ];
}

/** Env vars that stay out of app_settings. Values are never returned. */
function nexrec_settings_secret_env_keys(): array {
    return [
        'NEXREC_PGPASSWORD' => 'PostgreSQL password (bootstrap secret)',
        'NEXREC_DATA_DIR' => 'Data directory (bootstrap)',
        'NEXREC_ADMIN_USER' => 'Initial local admin (first boot only)',
        'NEXREC_ADMIN_PASSWORD' => 'Initial local admin password (first boot only)',
        'NEXREC_API_KEY' => 'Recorder API key',
        'NEXREC_LDAP_BIND_PASSWORD' => 'LDAP bind password',
        'NEXAPP_LAUNCH_SECRET' => 'WAN launch redeem secret',
        'NEXCLIP_ENROLLMENT_SECRET' => 'NexClip Mode 2 enrollment secret',
        'NEXCLIP_API_KEY' => 'Legacy NexClip API key',
        'NEXCLIP_NODE_TOKEN' => 'NexClip node bearer after register',
        'NEXREC_PUBLISH_JWT' => 'MediaMTX publish JWT',
        'NEXREC_HTTP_PORT' => 'HTTP listen port (process bootstrap)',
        'NEXREC_ALLOW_HTTP' => 'Allow plain HTTP (demo bootstrap)',
    ];
}

function nexrec_settings_env_raw(array $spec): ?string {
    $envKey = (string) ($spec['env'] ?? '');
    if ($envKey === '') {
        return null;
    }
    $v = getenv($envKey);
    if (!is_string($v) || $v === '') {
        return null;
    }
    $map = $spec['env_map'] ?? null;
    if (is_array($map) && isset($map[$v])) {
        return (string) $map[$v];
    }
    return $v;
}

function nexrec_settings_overrides_env(): bool {
    $v = getenv('NEXREC_ENV_OVERRIDES');
    return is_string($v) && in_array(strtolower($v), ['1', 'true', 'yes', 'on'], true);
}

function nexrec_settings_row(string $key): ?array {
    $st = nexrec_db()->prepare('SELECT key, value, updated_at, updated_by FROM app_settings WHERE key = :k');
    $st->bindValue(':k', $key, SQLITE3_TEXT);
    $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
    return $row === false ? null : $row;
}

function nexrec_settings_lookup(string $key): array {
    $cat = nexrec_settings_catalog();
    if (!isset($cat[$key])) {
        return ['value' => '', 'source' => 'missing'];
    }
    $spec = $cat[$key];
    if (nexrec_settings_overrides_env()) {
        $env = nexrec_settings_env_raw($spec);
        if ($env !== null) {
            return ['value' => $env, 'source' => 'env'];
        }
    }
    $row = nexrec_settings_row($key);
    if ($row !== null) {
        return ['value' => (string) $row['value'], 'source' => 'db'];
    }
    $env = nexrec_settings_env_raw($spec);
    if ($env !== null) {
        return ['value' => $env, 'source' => 'env'];
    }
    return ['value' => (string) ($spec['default'] ?? ''), 'source' => 'default'];
}

function nexrec_setting(string $key): string {
    $found = nexrec_settings_lookup($key);
    $val = $found['value'];
    if ($key === 'storage.recordings' && $val === '') {
        return rtrim(nexrec_data_dir(), '/') . '/storage';
    }
    if (($key === 'storage.exports' || $key === 'storage.scratch') && $val === '') {
        $root = nexrec_setting('storage.recordings');
        return $root . ($key === 'storage.exports' ? '/exports' : '/tmp');
    }
    return $val;
}

function nexrec_setting_int(string $key, int $fallback): int {
    $v = nexrec_setting($key);
    if ($v === '' || !preg_match('/^-?\d+$/', $v)) {
        return $fallback;
    }
    return (int) $v;
}

function nexrec_setting_bool(string $key): bool {
    $v = strtolower(nexrec_setting($key));
    return in_array($v, ['1', 'true', 'yes', 'on'], true);
}

function nexrec_settings_normalize(array $spec, string $val): string {
    $type = (string) ($spec['type'] ?? 'string');
    $val = str_replace("\0", '', $val);
    if ($type !== 'text') {
        $val = trim($val);
    } else {
        $val = trim($val, "\r\n");
    }
    switch ($type) {
        case 'bool':
            return in_array(strtolower($val), ['1', 'true', 'yes', 'on'], true) ? '1' : '0';
        case 'int':
            if (!preg_match('/^-?\d+$/', $val)) {
                throw new InvalidArgumentException('expected integer');
            }
            $n = (int) $val;
            $min = (int) ($spec['min'] ?? PHP_INT_MIN);
            $max = (int) ($spec['max'] ?? PHP_INT_MAX);
            if ($n < $min || $n > $max) {
                throw new InvalidArgumentException("out of range {$min}-{$max}");
            }
            return (string) $n;
        case 'enum':
            $opts = $spec['options'] ?? [];
            if (!in_array($val, $opts, true)) {
                throw new InvalidArgumentException('value not allowed');
            }
            return $val;
        case 'size':
            if (!preg_match('/^\d+(\.\d+)?\s*[KMGT]?B?$/i', $val)) {
                throw new InvalidArgumentException('expected a size like 50G or 512M');
            }
            $val = preg_replace('/\s+/', '', $val) ?? $val;
            return strtoupper($val);
        case 'bitrate':
            if (!preg_match('/^\d+(\.\d+)?[kKmMgG]?$/', $val)) {
                throw new InvalidArgumentException('expected a bitrate like 12M or 192k');
            }
            return $val;
        case 'path':
            if ($val === '') {
                return '';
            }
            if (!str_starts_with($val, '/') || str_contains($val, '..')) {
                throw new InvalidArgumentException('path must be absolute');
            }
            if (preg_match('/[\s;|&$`<>\n\r]/', $val)) {
                throw new InvalidArgumentException('path has illegal characters');
            }
            return $val;
        case 'url':
            if ($val === '') {
                return '';
            }
            if (!preg_match('#^[a-z][a-z0-9+.-]*://[^\s]+$#i', $val)) {
                throw new InvalidArgumentException('expected a URL');
            }
            if (preg_match('/[;|&$`<>\n\r]/', $val)) {
                throw new InvalidArgumentException('URL has illegal characters');
            }
            return $val;
        case 'secret_ref':
            if ($val === '') {
                return '';
            }
            if (!preg_match('/^env:[A-Z][A-Z0-9_]{0,64}$/', $val)) {
                throw new InvalidArgumentException('secret ref must look like env:NEXAPP_LAUNCH_SECRET');
            }
            return $val;
        case 'ident':
            if (!preg_match('/^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$/', $val)) {
                throw new InvalidArgumentException('invalid identifier');
            }
            return $val;
        case 'command':
            if ($val === '') {
                return '';
            }
            if (strlen($val) > 2000 || preg_match('/[\x00-\x1f`]/', $val)) {
                throw new InvalidArgumentException('command rejected');
            }
            return $val;
        case 'text':
            if (strlen($val) > 4000) {
                throw new InvalidArgumentException('text too long');
            }
            return $val;
        case 'string':
        default:
            if (strlen($val) > 500 || preg_match('/[\x00-\x1f]/', $val)) {
                throw new InvalidArgumentException('invalid string');
            }
            return $val;
    }
}

function nexrec_settings_seed(): int {
    nexrec_load_station_env();
    $n = 0;
    $now = nexrec_now_iso();
    foreach (nexrec_settings_catalog() as $key => $spec) {
        if (nexrec_settings_row($key) !== null) {
            continue;
        }
        $raw = nexrec_settings_env_raw($spec);
        if ($raw === null) {
            $raw = (string) ($spec['default'] ?? '');
        }
        try {
            $val = nexrec_settings_normalize($spec, $raw);
        } catch (InvalidArgumentException $e) {
            $val = nexrec_settings_normalize($spec, (string) ($spec['default'] ?? ''));
        }
        $st = nexrec_db()->prepare(
            'INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (:k, :v, :t, :by)'
        );
        $st->bindValue(':k', $key, SQLITE3_TEXT);
        $st->bindValue(':v', $val, SQLITE3_TEXT);
        $st->bindValue(':t', $now, SQLITE3_TEXT);
        $st->bindValue(':by', 'seed', SQLITE3_TEXT);
        $st->execute();
        $n++;
    }
    return $n;
}

function nexrec_settings_write_public_pem(string $pem): string {
    $pem = str_replace("\0", '', $pem);
    if (strlen($pem) > 20000 || !str_contains($pem, '-----BEGIN ') || !str_contains($pem, '-----END ')) {
        throw new InvalidArgumentException('PEM must include BEGIN and END markers');
    }
    if (preg_match('/[;|&$`]/', $pem)) {
        throw new InvalidArgumentException('PEM rejected');
    }
    $dir = nexrec_data_dir() . '/auth';
    if (!is_dir($dir) && !mkdir($dir, 0770, true) && !is_dir($dir)) {
        throw new RuntimeException('cannot create auth dir');
    }
    $path = $dir . '/nexapp-jwt-public.pem';
    if (file_put_contents($path, $pem) === false) {
        throw new RuntimeException('cannot write public key');
    }
    @chmod($path, 0640);
    return $path;
}

/**
 * @param array<string,mixed> $incoming
 * @return list<string> keys written
 */
function nexrec_settings_put(array $incoming, ?string $actor = null): array {
    nexrec_settings_seed();
    if (array_key_exists('nexapp.public_key_pem', $incoming)) {
        $pem = $incoming['nexapp.public_key_pem'];
        unset($incoming['nexapp.public_key_pem']);
        if (is_string($pem) && trim($pem) !== '') {
            $incoming['nexapp.public_key_path'] = nexrec_settings_write_public_pem($pem);
        }
    }
    $cat = nexrec_settings_catalog();
    $updated = [];
    $now = nexrec_now_iso();
    $by = $actor !== null && $actor !== '' ? $actor : 'admin';
    foreach ($incoming as $key => $raw) {
        if (!is_string($key) || !isset($cat[$key])) {
            throw new InvalidArgumentException('unknown setting ' . (is_string($key) ? $key : ''));
        }
        if (is_array($raw) || is_object($raw)) {
            throw new InvalidArgumentException('invalid value for ' . $key);
        }
        $val = nexrec_settings_normalize($cat[$key], (string) $raw);
        $st = nexrec_db()->prepare(
            'INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (:k,:v,:t,:by)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by'
        );
        $st->bindValue(':k', $key, SQLITE3_TEXT);
        $st->bindValue(':v', $val, SQLITE3_TEXT);
        $st->bindValue(':t', $now, SQLITE3_TEXT);
        $st->bindValue(':by', $by, SQLITE3_TEXT);
        $st->execute();
        $updated[] = $key;
    }
    return $updated;
}

function nexrec_settings_public(): array {
    nexrec_settings_seed();
    $sections = nexrec_settings_sections();
    $fields = [];
    $flat = [];
    foreach (nexrec_settings_catalog() as $key => $spec) {
        $found = nexrec_settings_lookup($key);
        $value = nexrec_setting($key);
        $section = (string) $spec['section'];
        $flat[$key] = $value;
        $field = [
            'key' => $key,
            'value' => $value,
            'source' => $found['source'],
            'section' => $section,
            'section_label' => $sections[$section] ?? $section,
            'label' => (string) $spec['label'],
            'type' => (string) $spec['type'],
            'help' => (string) ($spec['help'] ?? ''),
        ];
        if (!empty($spec['options'])) {
            $field['options'] = array_values($spec['options']);
        }
        if (!empty($spec['ui'])) {
            $field['ui'] = (string) $spec['ui'];
        }
        $fields[] = $field;
    }
    $secrets = [];
    foreach (nexrec_settings_secret_env_keys() as $envKey => $note) {
        $v = getenv($envKey);
        $set = is_string($v) && $v !== '';
        $secrets[] = [
            'key' => $envKey,
            'note' => $note,
            'set' => $set,
        ];
    }
    return [
        'settings' => $flat,
        'fields' => $fields,
        'secrets' => $secrets,
        'policy' => 'Day-to-day settings live in Postgres app_settings (this page). nexrec.env is bootstrap and secrets only, including the database password. Set NEXREC_ENV_OVERRIDES=1 to let the env file win for one boot.',
    ];
}
