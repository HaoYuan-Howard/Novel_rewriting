#Requires -Version 5.1
<#
.SYNOPSIS
    起「小说重构 · 审校台」。双击 tools\start.cmd 即可，一个窗口、一个地址。

.DESCRIPTION
    默认形态是**单服务**：`novel/api/app.py` 把 `web/dist` 挂在自己那个端口上，
    所以界面和 API 同源同端口，没有 Vite dev server、没有 proxy、没有第二个进程。

      [1/3] dist 比 src 旧 → npm run build
      [2/3] 起后端 127.0.0.1:8765（前台，Ctrl+C 即全停）
      [3/3] 端口一通就打开浏览器

    `-Dev` 换成开发形态：后端后台 + Vite dev server 前台，前端改代码即时热更。
    那条路才需要 `web/vite.config.ts` 里指向 8765 的 proxy。

    ⚠ 本脚本不碰绑定地址。后端绑哪儿由 `novel/api/app.py::HOST` 一处决定
    （`R-11.1`），这里只传 `--base`。

.PARAMETER Base
    语料根目录（`--base <Output目录>`，outputs 下含各书目录的那一层）。
    省略时取 outputs 下最近改动过的那个。

.PARAMETER Dev
    开发形态：Vite dev server（热更）+ 后台后端。

.PARAMETER Port
    仅 `-Dev` 下的前端端口，默认 5173。生产形态的端口是后端自己的 8765。

.PARAMETER NoBuild
    跳过构建新鲜度检查，直接用现有 `web/dist`。

.PARAMETER NoOpen
    别自动打开浏览器。

.PARAMETER Install
    强制重装 web 依赖（默认只在 node_modules 缺失时装）。

.EXAMPLE
    .\tools\start.ps1
.EXAMPLE
    .\tools\start.ps1 -Base outputs\e2e_20260817_jiaoren
.EXAMPLE
    .\tools\start.ps1 -Dev
#>
[CmdletBinding()]
param(
    [string] $Base,
    [int]    $Port = 5173,
    [switch] $Dev,
    [switch] $NoBuild,
    [switch] $NoOpen,
    [switch] $Install
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WebDir   = Join-Path $RepoRoot 'web'
$DistDir  = Join-Path $WebDir 'dist'
$OutRoot  = Join-Path $RepoRoot 'outputs'

# novel/api/app.py 的 PORT。生产形态下这就是界面地址的端口；
# 开发形态下它是 web/vite.config.ts 那条 proxy 的 target。
$ApiPort  = 8765
$CondaEnv = 'novel_refactor'

function Write-Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Note([string]$m) { Write-Host "    $m" -ForegroundColor DarkGray }
function Write-Warn([string]$m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Write-Bad ([string]$m) { Write-Host "xx  $m" -ForegroundColor Red }

function Test-PortListening([int]$p) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    return $null -ne $c
}

function Resolve-Npm {
    # ⚠ 必须绕开 `npm.ps1`。Node 装出来的那个 PowerShell shim 在 5.1 下会把参数搅烂：
    #   & npm run build   →   npm 那头收到的是   Unknown command: "pm"
    # 同一组参数交给 npm.cmd 就正常。-CommandType Application 把 .ps1 挡在外面
    # （它算 ExternalScript，不算 Application）。
    foreach ($n in @('npm.cmd', 'npm.exe')) {
        $c = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue |
             Select-Object -First 1
        if ($c) { return $c.Source }
    }
    $c = Get-Command 'npm' -CommandType Application -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Resolve-EnvPython([string]$envName) {
    <#
      直接拿到环境里的 python.exe，而不是每次都套一层 `conda run`。
      ⚠ 理由不是省那两秒：`conda run` 是个中间进程，Ctrl+C 到不了里面的 uvicorn，
      于是「一个窗口、Ctrl+C 全停」这条就不成立了。解析不出来时才退回 conda run。
    #>
    $lines = & conda env list 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $lines) { return $null }
    foreach ($line in $lines) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*(\S+)\s+\*?\s*([A-Za-z]:\\.*?)\s*$') {
            if ($Matches[1] -eq $envName) {
                $py = Join-Path $Matches[2] 'python.exe'
                if (Test-Path -LiteralPath $py) { return $py }
            }
        }
    }
    return $null
}

function Resolve-Base([string]$requested) {
    if ($requested) {
        try {
            $p = (Resolve-Path -LiteralPath $requested -ErrorAction Stop).Path
        } catch {
            Write-Bad "-Base 指的路径不存在：$requested"
            exit 2
        }
        if (-not (Test-Path -LiteralPath $p -PathType Container)) {
            Write-Bad "-Base 不是一个目录：$p"
            exit 2
        }
        return $p
    }
    $cand = $null
    if (Test-Path -LiteralPath $OutRoot) {
        $cand = Get-ChildItem -LiteralPath $OutRoot -Directory -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }
    if (-not $cand) {
        Write-Bad 'outputs\ 下没有可用的语料目录，拿 -Base 显式指一个。'
        exit 2
    }
    Write-Note "没给 -Base，取 outputs 下最近改动的：$($cand.Name)"
    return $cand.FullName
}

function Test-BuildIsStale {
    <#
      dist 比源码旧吗。⚠ 只看时间戳，不做内容哈希：这一格判错的代价是
      「多构建一次」（十几秒），而反过来——判成新鲜、界面停在上一版——
      是那种会让人怀疑自己改的代码没生效的错。宁可多构建。
    #>
    $index = Join-Path $DistDir 'index.html'
    if (-not (Test-Path -LiteralPath $index)) { return $true }
    $built = (Get-Item -LiteralPath $index).LastWriteTime

    $sources = @()
    $srcDir = Join-Path $WebDir 'src'
    if (Test-Path -LiteralPath $srcDir) {
        $sources += Get-ChildItem -LiteralPath $srcDir -Recurse -File -ErrorAction SilentlyContinue
    }
    foreach ($f in @('index.html', 'package.json', 'package-lock.json',
                     'vite.config.ts', 'tsconfig.json')) {
        $p = Join-Path $WebDir $f
        if (Test-Path -LiteralPath $p) { $sources += Get-Item -LiteralPath $p }
    }
    foreach ($s in $sources) {
        if ($s.LastWriteTime -gt $built) {
            Write-Note "源码比构建产物新：$($s.Name)"
            return $true
        }
    }
    return $false
}

function Invoke-Npm([string[]]$npmArgs) {
    <#
      ⚠ `| Out-Host` 不是为了好看：PowerShell 的函数返回的是**所有没被消费的输出**，
      不加这一道的话 npm 那几十行 stdout 会和 `$LASTEXITCODE` 一起成为返回值，
      调用方拿到的 `$rc` 是个数组，`-ne 0` 恒真 ⇒ **构建成功也报失败**。
      （实测踩过：vite 明明打了「✓ built in 1.78s」，脚本判红。）
    #>
    Push-Location $WebDir
    try {
        & $script:Npm @npmArgs | Out-Host
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

function Start-BrowserWhenReady([string]$url, [int]$waitPort) {
    <#
      端口一通就开浏览器。⚠ 做成后台 job 是因为后端要跑在**前台**
      （那是 Ctrl+C 能停住它的唯一形态），所以「起完再开浏览器」这句话
      在主线程里没有位置。
    #>
    Start-Job -ScriptBlock {
        param($u, $p)
        for ($i = 0; $i -lt 120; $i++) {
            if (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) {
                Start-Sleep -Milliseconds 400   # 端口在听 ≠ 路由已挂好
                Start-Process $u
                return
            }
            Start-Sleep -Milliseconds 250
        }
    } -ArgumentList $url, $waitPort | Out-Null
}

# ---------------------------------------------------------------- 0. 前置

if (-not (Test-Path -LiteralPath (Join-Path $WebDir 'package.json'))) {
    Write-Bad "没找到 $WebDir\package.json——这个脚本得待在仓库的 tools\ 底下跑。"
    exit 2
}

$Python = Resolve-EnvPython $CondaEnv
$UseCondaRun = $false
if (-not $Python) {
    if (Get-Command conda -CommandType Application -ErrorAction SilentlyContinue) {
        Write-Warn "没能从 conda 环境 $CondaEnv 里解析出 python.exe，退回 conda run（Ctrl+C 可能要按两次）。"
        $UseCondaRun = $true
    } else {
        Write-Bad "没找到 conda 环境 $CondaEnv 的 python，也没找到 conda 命令。"
        Write-Note "后端跑在 conda 环境 $CondaEnv 里（见 README「运行与验证」）。"
        exit 127
    }
}

$Base = Resolve-Base $Base

# ---------------------------------------------------------------- 1. 前端产物

$Npm = Resolve-Npm
$needBuild = -not $NoBuild -and -not $Dev

if ($needBuild -or $Install) {
    if (-not $Npm) {
        Write-Bad '没找到 npm，构建不了前端。装个 Node.js（仓里实测 node 22 / npm 10）。'
        Write-Note '已经有 web\dist 的话可以加 -NoBuild 直接用它。'
        exit 127
    }
    $viteMark = Join-Path $WebDir 'node_modules\vite\package.json'
    if ($Install -or -not (Test-Path -LiteralPath $viteMark)) {
        if (Test-Path -LiteralPath (Join-Path $WebDir 'package-lock.json')) {
            $cmd = 'ci'
        } else {
            $cmd = 'install'
        }
        Write-Step "[1/3] 装 web 依赖：npm $cmd"
        $rc = Invoke-Npm @($cmd)
        if ($rc -ne 0) { Write-Bad "npm $cmd 失败（exit $rc）。"; exit $rc }
    }
}

if ($Dev) {
    Write-Note '开发形态：不构建，走 Vite dev server 的热更。'
} elseif ($NoBuild) {
    Write-Note '按 -NoBuild 跳过构建，直接用现有 web\dist。'
} elseif (Test-BuildIsStale) {
    Write-Step '[1/3] 构建前端：npm run build'
    $rc = Invoke-Npm @('run', 'build')
    if ($rc -ne 0) {
        Write-Bad "构建失败（exit $rc）。上面是 vite 的报错。"
        exit $rc
    }
} else {
    Write-Note '[1/3] web\dist 是新的，不用重新构建。'
}

# ---------------------------------------------------------------- 2. 后端

$apiArgs = @('-X', 'utf8', '-u', '-m', 'novel.api', '--base', $Base)

if (Test-PortListening $ApiPort) {
    # 已经有一个在跑。⚠ 不再起第二个：那只会得到一个「端口被占用」的堆栈，
    # 而人想要的显然是「把已经开着的那个给我看」。
    Write-Note "[2/3] $ApiPort 上已经有服务在听，复用它（没有起新进程）。"
    $alreadyUp = $true
} else {
    $alreadyUp = $false
}

if ($Dev) {
    # 开发形态：后端进后台窗口，前台留给 Vite。
    if (-not $alreadyUp) {
        Write-Step "[2/3] 起后端（独立窗口）：127.0.0.1:$ApiPort    base=$Base"
        if ($UseCondaRun) {
            $conda = (Get-Command conda -CommandType Application).Source
            Start-Process -FilePath $conda -WorkingDirectory $RepoRoot -WindowStyle Normal `
                -ArgumentList (@('run', '-n', $CondaEnv, '--no-capture-output', 'python') + $apiArgs) | Out-Null
        } else {
            Start-Process -FilePath $Python -WorkingDirectory $RepoRoot -WindowStyle Normal `
                -ArgumentList $apiArgs | Out-Null
        }
        $waited = 0
        while ($waited -lt 60 -and -not (Test-PortListening $ApiPort)) {
            Start-Sleep -Milliseconds 500
            $waited++
        }
        if (Test-PortListening $ApiPort) {
            Write-Note "后端就绪：http://127.0.0.1:$ApiPort/"
        } else {
            Write-Warn "等了 30 秒 $ApiPort 还没听上。去那个新窗口看它报了什么；前端照常起。"
        }
    }

    if (-not $Npm) { Write-Bad '没找到 npm，起不了 Vite dev server。'; exit 127 }
    if (Test-PortListening $Port) {
        Write-Warn "$Port 已被占用，Vite 会自动往后挪一个端口。"
    }
    if (-not $NoOpen) { Start-BrowserWhenReady "http://127.0.0.1:$Port/" $Port }

    Write-Step "[3/3] 起 Vite dev server：http://127.0.0.1:$Port    Ctrl+C 停"
    $devArgs = @('run', 'dev', '--', '--port', "$Port")
    exit (Invoke-Npm $devArgs)
}

# 生产形态：后端占前台，Ctrl+C 直接停它 —— 一个窗口的全部含义就在这一行。
$url = "http://127.0.0.1:$ApiPort/"

if ($alreadyUp) {
    if (-not $NoOpen) { Start-Process $url }
    Write-Step "[3/3] 界面：$url"
    Write-Note '服务是之前那个进程在跑，本窗口没有接管它——关掉这里不会停服务。'
    exit 0
}

if (-not $NoOpen) { Start-BrowserWhenReady $url $ApiPort }

Write-Step "[2/3] 起服务（界面 + API 同一个端口）：$url"
Write-Note "base=$Base"
Write-Step "[3/3] 浏览器一会儿会自己打开。这个窗口按 Ctrl+C 就是全停。"

Push-Location $RepoRoot
try {
    if ($UseCondaRun) {
        & conda run -n $CondaEnv --no-capture-output python @apiArgs
    } else {
        & $Python @apiArgs
    }
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
    Get-Job -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
}
exit $rc
