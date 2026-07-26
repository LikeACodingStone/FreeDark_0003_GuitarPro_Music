param(
    [switch]$DryRun,
    [switch]$ResetAccount
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed or not in PATH."
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker Compose v2 is not available. Please install the docker compose plugin."
}

if ((-not (Test-Path ".env")) -and $ResetAccount) {
    New-Item -ItemType File -Path ".env" | Out-Null
}
elseif (-not (Test-Path ".env")) {
    Write-Host "Creating .env. Telegram login password will not be stored."
    $tgApiId = Read-Host "TG_API_ID"
    $tgApiHashSecure = Read-Host "TG_API_HASH" -AsSecureString
    $tgApiHashPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tgApiHashSecure)
    try {
        $tgApiHash = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tgApiHashPtr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tgApiHashPtr)
    }
    $tgPhone = Read-Host "TG_PHONE (optional, include country code)"

    $tgApiId = $tgApiId.Trim()
    $tgApiHash = $tgApiHash.Trim()
    $tgPhone = $tgPhone.Trim()

    if ($tgApiId -notmatch '^[0-9]+$') {
        Write-Error "TG_API_ID must be the numeric api_id from https://my.telegram.org/apps."
    }

    if ($tgApiHash -notmatch '^[0-9a-fA-F]{32}$') {
        Write-Error "TG_API_HASH must be the 32-character api_hash from https://my.telegram.org/apps, not a bot token."
    }

    $envContent = @(
        "TG_API_ID=$tgApiId",
        "TG_API_HASH=$tgApiHash",
        "TG_PHONE=$tgPhone",
        "TG_BOT_USERNAME=SQMP3",
        "",
        "SESSION_TTL_HOURS=72",
        "MIN_DELAY=60",
        "MAX_DELAY=150",
        "RESPONSE_TIMEOUT=120",
        "LONG_REST_EVERY_MINUTES=120",
        "LONG_REST_MIN_MINUTES=30",
        "LONG_REST_MAX_MINUTES=90",
        "STOP_ON_SEND_BLOCKED=1",
        "SEND_BLOCKED_COOLDOWN_HOURS=6",
        "SUCCESS_CONFIRM_EVERY=500"
    )
    Set-Content -Path ".env" -Value $envContent -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path "TMDownload" | Out-Null
New-Item -ItemType Directory -Force -Path "telegram_session" | Out-Null

docker compose build

$runArgs = @("compose", "run", "--rm", "telegram-downloader", "python", "/app/telegram_downloader.py")
if ($DryRun) {
    $runArgs += "--dry-run"
}
if ($ResetAccount) {
    $runArgs += "--reset-account-only"
}

& docker @runArgs
