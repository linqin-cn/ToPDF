<#
.SYNOPSIS
    构建镜像并以真实 HTTP 请求验证服务的最小可用性。

.DESCRIPTION
    步骤：构建镜像 → 启动容器 → 轮询存活探针 → 校验接口与错误码 → 输出容器日志 → 清理。
    任一检查失败即以非零码退出，便于在 CI 与本地复用。

.EXAMPLE
    ./scripts/smoke.ps1 -Port 18080
#>

[CmdletBinding()]
param(
    [string]$Image = "resume-pdf-renderer:smoke",
    [string]$Container = "rpr-smoke",
    [int]$Port = 18080,
    [int]$WaitSeconds = 60,
    [string]$ApiKey = "smoke-key"
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:$Port"
$failures = @()

function Write-Step([string]$message) {
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Invoke-Check {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    try {
        & $Action
        Write-Host "  [PASS] $Name" -ForegroundColor Green
    }
    catch {
        $failures += "$Name : $($_.Exception.Message)"
        Write-Host "  [FAIL] $Name : $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Step "构建镜像 $Image"
docker build -t $Image .
if ($LASTEXITCODE -ne 0) { throw "镜像构建失败" }

Write-Step "启动容器 $Container（端口 $Port）"
docker rm -f $Container 2>$null | Out-Null
docker run -d --rm --name $Container -p "${Port}:8000" -e "RPR_API_KEYS=$ApiKey" $Image | Out-Null
if ($LASTEXITCODE -ne 0) { throw "容器启动失败" }

try {
    Write-Step "等待服务就绪（最长 $WaitSeconds 秒）"
    $ready = $false
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $probe = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 5
            if ($probe.status -eq "ok") { $ready = $true; break }
        }
        catch {
            Start-Sleep -Milliseconds 800
        }
    }
    if (-not $ready) { throw "服务未在 $WaitSeconds 秒内就绪" }

    Write-Step "执行接口检查"
    Invoke-Check "GET /healthz 返回 200 且 status=ok" {
        $response = Invoke-WebRequest -Uri "$baseUrl/healthz" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ne 200) { throw "状态码 $($response.StatusCode)" }
        $body = $response.Content | ConvertFrom-Json
        if ($body.status -ne "ok") { throw "status=$($body.status)" }
        if (-not $body.version) { throw "缺少 version 字段" }
    }

    Invoke-Check "未知路径返回 404" {
        try {
            Invoke-WebRequest -Uri "$baseUrl/not-exists" -UseBasicParsing -TimeoutSec 5 | Out-Null
            throw "预期 404，实际成功"
        }
        catch {
            $status = $_.Exception.Response.StatusCode.value__
            if ($status -ne 404) { throw "预期 404，实际 $status" }
        }
    }
}
finally {
    Write-Step "容器日志"
    docker logs $Container 2>&1 | Select-Object -Last 20
    Write-Step "清理容器"
    docker rm -f $Container 2>$null | Out-Null
}

if ($failures.Count -gt 0) {
    Write-Host "冒烟失败：" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "冒烟通过" -ForegroundColor Green
