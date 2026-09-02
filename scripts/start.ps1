#Requires -Version 5.1
<#
.SYNOPSIS
    起「小说重构 · 审校台」。双击 scripts\start.cmd 即可，一个窗口、一个地址。

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
    语料根目录（`--base <Output目录>`，**含各书目录的那一层**）。
    省略时就是仓根的 `outputs\` 本身——语料库屏列的是它下面的全部书。

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

.PARAMETER Restart
    8765 已有本审校台服务时，先确认没有 running/paused Agent、待启动 Agent 或
    running API 作业，再停止旧 Python 进程并用当前代码重新启动。默认不传时仍复用旧服务。

.EXAMPLE
    .\scripts\start.ps1
.EXAMPLE
    .\scripts\start.ps1 -Base D:\另一个语料根
.EXAMPLE
    .\scripts\start.ps1 -Dev
.EXAMPLE
    .\scripts\start.ps1 -Restart
#>
[CmdletBinding()]
param(
    [string] $Base,
    [int]    $Port = 5173,
    [switch] $Dev,
    [switch] $NoBuild,
    [switch] $NoOpen,
    [switch] $Install,
    [switch] $Restart
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

function Get-ListeningTcpProcess([int]$p) {
    <#
      Get-NetTCPConnection 在部分普通用户 / agent 沙箱里会直接 AccessDenied；若把那个
      失败吞成「端口没监听」，下一步只会再起一个必然撞端口的 uvicorn。netstat 是这里
      更窄的读面：只解析 TCP LISTENING 行，并把 PID 与本地地址一起保留给重启身份检查。
    #>
    $seen = @{}
    $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
    foreach ($line in (& $netstat -ano -p TCP 2>$null)) {
        $fields = @($line.Trim() -split '\s+')
        if ($fields.Count -lt 5 -or $fields[0] -ne 'TCP' -or
            $fields[3] -ne 'LISTENING' -or
            -not $fields[1].EndsWith(":$p")) {
            continue
        }
        $listenerPid = 0
        if (-not [int]::TryParse($fields[4], [ref]$listenerPid)) { continue }
        $identity = "$($fields[1])|$listenerPid"
        if ($seen.ContainsKey($identity)) { continue }
        $seen[$identity] = $true
        [pscustomobject]@{
            LocalAddress = $fields[1]
            ProcessId = $listenerPid
        }
    }
}

function Test-PortListening([int]$p) {
    return @(Get-ListeningTcpProcess $p).Count -gt 0
}

function Get-NovelServiceActivity([int]$p) {
    <#
      HTTP 形状同时证明「这是审校台」与「当前可安全停」。不能只凭 python.exe + 8765
      就杀进程：同一解释器可以承载别的本机服务，而进程名不是产品身份。
    #>
    $root = "http://127.0.0.1:$p/api"
    try {
        $agentQueue = Invoke-RestMethod -Method Get -TimeoutSec 5 `
            -Uri "$root/agent/runs/queue"
        $agentRuns = Invoke-RestMethod -Method Get -TimeoutSec 5 `
            -Uri "$root/agent/runs"
        $jobs = Invoke-RestMethod -Method Get -TimeoutSec 5 -Uri "$root/jobs"
    } catch {
        throw "端口 $p 上的服务无法通过审校台只读 API 验明身份：$($_.Exception.Message)"
    }
    $shapeChecks = @(
        [pscustomobject]@{ Value = $agentQueue; Names = @('running', 'pending') },
        [pscustomobject]@{ Value = $agentRuns; Names = @('runs') },
        [pscustomobject]@{ Value = $jobs; Names = @('jobs') }
    )
    foreach ($required in $shapeChecks) {
        $value = $required.Value
        foreach ($name in $required.Names) {
            if ($null -eq $value -or
                -not ($value.PSObject.Properties.Name -contains $name)) {
                throw "端口 $p 返回的不是当前审校台 API 形状（缺 $name）"
            }
        }
    }
    $activeAgents = @($agentRuns.runs | Where-Object {
        $_.state -in @('pending', 'running', 'paused')
    })
    $pendingAgents = @($agentQueue.pending)
    $activeJobs = @($jobs.jobs | Where-Object {
        $_.state -in @('pending', 'running')
    })
    return [pscustomobject]@{
        ActiveAgents = $activeAgents
        PendingAgents = $pendingAgents
        ActiveJobs = $activeJobs
    }
}

function Stop-IdleNovelService([int]$p, [string]$expectedPython) {
    $listeners = @(Get-ListeningTcpProcess $p)
    if ($listeners.Count -ne 1) {
        throw "端口 $p 应恰有一个监听进程，实际 $($listeners.Count) 个；拒绝猜测该停谁"
    }
    $listener = $listeners[0]
    if ($listener.LocalAddress -ne "127.0.0.1:$p") {
        throw "端口 $p 的监听地址是 $($listener.LocalAddress)，不是审校台冻结的 127.0.0.1；拒绝停止"
    }

    $activity = Get-NovelServiceActivity $p
    if ($activity.ActiveAgents.Count -gt 0 -or
        $activity.PendingAgents.Count -gt 0 -or
        $activity.ActiveJobs.Count -gt 0) {
        throw (
            "拒绝重启：active agent=$($activity.ActiveAgents.Count)，" +
            "queued agent=$($activity.PendingAgents.Count)，" +
            "active API job=$($activity.ActiveJobs.Count)。先在界面安全闭合这些任务。")
    }

    try {
        $process = Get-Process -Id $listener.ProcessId -ErrorAction Stop
    } catch {
        throw "监听进程 PID $($listener.ProcessId) 在身份检查后消失；请重新执行 -Restart"
    }
    if ($process.ProcessName -ne 'python') {
        throw "PID $($listener.ProcessId) 是 $($process.ProcessName)，不是审校台 Python；拒绝停止"
    }
    if ($expectedPython) {
        $actualPath = $process.Path
        if (-not $actualPath -or
            -not [string]::Equals(
                [IO.Path]::GetFullPath($actualPath),
                [IO.Path]::GetFullPath($expectedPython),
                [StringComparison]::OrdinalIgnoreCase)) {
            throw "PID $($listener.ProcessId) 的解释器不是环境 $CondaEnv；拒绝停止"
        }
    }

    # 最后一刻重读 listener，避免 HTTP 检查期间 PID/端口换主后杀到新进程。
    $current = @(Get-ListeningTcpProcess $p)
    if ($current.Count -ne 1 -or
        $current[0].ProcessId -ne $listener.ProcessId -or
        $current[0].LocalAddress -ne $listener.LocalAddress) {
        throw "端口 $p 的监听身份在检查期间变化；请重新执行 -Restart"
    }

    Write-Step "[2/3] 停止旧审校台：PID $($listener.ProcessId)"
    Stop-Process -Id $listener.ProcessId -ErrorAction Stop
    for ($i = 0; $i -lt 50 -and (Test-PortListening $p); $i++) {
        Start-Sleep -Milliseconds 200
    }
    if (Test-PortListening $p) {
        throw "PID $($listener.ProcessId) 已收到停止请求，但端口 $p 在 10 秒后仍监听"
    }
    Write-Note '旧服务已停止；下面会用当前磁盘代码启动新进程。'
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
    # ⚠ 默认取 `outputs\` **本身**，不是它下面某个子目录。
    #   `--base` 的语义是「**含各书目录的那一层**」（`novel/paths.py::iter_novel_dirs`），
    #   语料库屏 `GET /api/novels` 列的就是它下面的全部书。
    # ⚠⚠ 这里原来取的是「outputs 下最近改动的那个**子目录**」，于是每次起服务
    #   都只挑得到一本书——当时盘上是一批次一目录、每目录一本，而人想要的显然是
    #   「进来先看见所有书，自己挑一本」。挑批次这件事仍然做得到：`-Base` 指过去就行。
    if (-not (Test-Path -LiteralPath $OutRoot -PathType Container)) {
        Write-Bad "没找到 $OutRoot——先跑一本书建出来，或拿 -Base 指一个语料根。"
        exit 2
    }
    return (Resolve-Path -LiteralPath $OutRoot).Path
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
    if ($Restart) {
        try {
            Stop-IdleNovelService $ApiPort $Python
        } catch {
            Write-Bad $_.Exception.Message
            exit 3
        }
        $alreadyUp = $false
    } else {
        # 默认双击仍只「启动或打开」：若正在付费跑书，第二次双击不能把它当残留进程杀掉。
        # 后端代码变化后要显式使用 -Restart；它会先验服务身份与三类活动任务。
        Write-Note "[2/3] $ApiPort 上已经有服务在听，复用它（没有起新进程）。"
        Write-Note '要加载新的后端代码，请运行 scripts\start.cmd -Restart，或双击 scripts\restart.cmd。'
        $alreadyUp = $true
    }
} else {
    if ($Restart) {
        Write-Note "[2/3] $ApiPort 没有旧服务；直接用当前代码启动。"
    }
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
