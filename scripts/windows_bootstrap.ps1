# Windows PowerShell 5.1 bootstrap: runs even when Python/py is missing.
[CmdletBinding()]
param([ValidateSet('Setup','Launch')][string]$Mode = 'Setup', [switch]$LibraryOnly,
      [switch]$PullModel, [switch]$Check, [switch]$NoBrowser,
      [Parameter(ValueFromRemainingArguments=$true)][string[]]$LegacyArgs)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:PythonRelease = '3.12.10' # Last official 3.12 Windows installer release.
$script:PythonUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
$script:PythonPublisher = 'Python Software Foundation'

function Confirm-Step([string]$Message) {
    return (Read-Host "$Message [y/N]") -match '^(?i:y|yes)$'
}

function Invoke-Probe([string]$Executable, [string]$Prefix = '') {
    # Probe the actual executable; launcher/PATH names alone prove nothing.
    $code = "import sys,struct,json,venv,ensurepip;print(json.dumps({'executable':sys.executable,'major':sys.version_info.major,'minor':sys.version_info.minor,'micro':sys.version_info.micro,'bits':struct.calcsize('P')*8,'implementation':sys.implementation.name,'base':sys.prefix==sys.base_prefix}))"
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = "$Prefix -I -c `"$code`""
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { return $null }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(8000)) {
            $process.Kill() # Only this owned probe, never an unrelated process.
            return $null
        }
        if ($process.ExitCode -ne 0) { return $null }
        return $stdout.Result.Trim() | ConvertFrom-Json
    } catch { return $null } finally { $process.Dispose() }
}

function Test-Compatible($Probe, [switch]$AllowVenv) {
    if ($null -eq $Probe) { return $false }
    try {
        return $Probe.major -eq 3 -and $Probe.minor -in @(11,12) -and
            $Probe.bits -eq 64 -and $Probe.implementation -eq 'cpython' -and
            ($AllowVenv -or $Probe.base) -and (Test-Path -LiteralPath $Probe.executable -PathType Leaf)
    } catch { return $false }
}

function Select-Python([object[]]$Candidates) {
    $found = @()
    $seen = @{}
    foreach ($candidate in $Candidates) {
        $key = "$($candidate.Executable)|$($candidate.Prefix)".ToLowerInvariant()
        if ($seen.ContainsKey($key) -or $candidate.Executable -match '\\WindowsApps\\') { continue }
        $seen[$key] = $true
        $probe = Invoke-Probe $candidate.Executable $candidate.Prefix
        if (Test-Compatible $probe) { $found += $probe }
    }
    # 3.12 wins even when PATH, registry or a launcher first offers 3.13/3.11.
    return $found | Sort-Object -Property @{Expression={$_.minor};Descending=$true},
        @{Expression={$_.micro};Descending=$true} | Select-Object -First 1
}

function Get-PythonCandidates {
    $items = @()
    # PEP 514: per-user/system, both registry views, all registered companies/tags.
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)) {
            $base = $null; $python = $null
            try {
                $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
                $python = $base.OpenSubKey('Software\Python')
                if ($null -eq $python) { continue }
                foreach ($companyName in $python.GetSubKeyNames()) {
                    $company = $python.OpenSubKey($companyName)
                    try {
                        foreach ($tag in $company.GetSubKeyNames()) {
                            $install = $company.OpenSubKey("$tag\InstallPath")
                            if ($null -eq $install) { continue }
                            try {
                                $exe = $install.GetValue('ExecutablePath')
                                if (-not $exe) {
                                    $folder = $install.GetValue('')
                                    if ($folder) { $exe = Join-Path $folder 'python.exe' }
                                }
                                if ($exe) { $items += [pscustomobject]@{Executable=[string]$exe;Prefix=''} }
                            } finally { $install.Dispose() }
                        }
                    } finally { $company.Dispose() }
                }
            } catch { Write-Verbose "Registry view unavailable: $view" }
            finally { if ($python) {$python.Dispose()}; if ($base) {$base.Dispose()} }
        }
    }
    $folders = @()
    if ($env:LOCALAPPDATA) {
        $folders += Join-Path $env:LOCALAPPDATA 'PerpetualBistro\Python312'
        $folders += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*') -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName)
    }
    foreach ($programs in @($env:ProgramFiles,$env:ProgramW6432,${env:ProgramFiles(x86)})) {
        if ($programs) { $folders += @(Get-ChildItem -Path (Join-Path $programs 'Python*') -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName) }
    }
    if ($env:SystemDrive) { $folders += "$env:SystemDrive\Python311", "$env:SystemDrive\Python312" }
    foreach ($folder in $folders) { $items += [pscustomobject]@{Executable=(Join-Path $folder 'python.exe');Prefix=''} }
    foreach ($folder in ($env:PATH -split ';')) {
        $folder = $folder.Trim('"')
        if (-not $folder) { continue }
        foreach ($name in @('python.exe','python3.exe','python3.12.exe','python3.11.exe')) {
            $exe = Join-Path $folder $name
            if (Test-Path -LiteralPath $exe -PathType Leaf) { $items += [pscustomobject]@{Executable=$exe;Prefix=''} }
        }
    }
    foreach ($launcher in @(Get-Command py.exe -All -ErrorAction SilentlyContinue)) {
        foreach ($tag in @('-3.12','-3.11')) { $items += [pscustomobject]@{Executable=$launcher.Source;Prefix=$tag} }
    }
    return $items
}

function Receive-OfficialPython([string]$Destination) {
    # No redirect may leave HTTPS python.org. Do not disable certificate validation.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $uri = [uri]$script:PythonUrl
    for ($hop=0; $hop -lt 5; $hop++) {
        if ($uri.Scheme -ne 'https' -or $uri.Host -notin @('www.python.org','python.org')) {
            throw 'Installer download was redirected outside official HTTPS python.org. Nothing was installed.'
        }
        $request = [Net.HttpWebRequest]::Create($uri)
        $request.AllowAutoRedirect = $false
        $request.Timeout = 30000
        $request.ReadWriteTimeout = 30000
        $response = $null
        try {
            $response = $request.GetResponse()
            $status = [int]$response.StatusCode
            if ($status -in @(301,302,303,307,308)) {
                $uri = [uri]::new($uri, $response.Headers['Location'])
                continue
            }
            if ($status -ne 200) { throw 'Python download did not return a successful response.' }
            $stream = $response.GetResponseStream()
            $file = [IO.File]::Open($Destination,[IO.FileMode]::CreateNew)
            try {
                $buffer = New-Object byte[] 65536
                $total = 0; $timer = [Diagnostics.Stopwatch]::StartNew()
                while (($count = $stream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $total += $count
                    if ($total -gt 100MB -or $timer.Elapsed.TotalMinutes -gt 5) { throw 'Python download exceeded its size/time limit.' }
                    $file.Write($buffer,0,$count)
                }
                if ($total -eq 0) { throw 'The Python download was empty.' }
            } finally { $file.Dispose(); $stream.Dispose() }
            return
        } finally { if ($response) { $response.Dispose() } }
    }
    throw 'Too many redirects downloading Python. Please retry setup later.'
}

function Assert-PythonSignature([string]$Installer) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Installer
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false) -cne $script:PythonPublisher) {
        throw 'Python installer signature or publisher verification failed. Nothing was installed. Check Windows date/certificate updates and retry.'
    }
    # Windows validates the file digest and certificate chain; pin the expected publisher.
}

function Invoke-PythonInstaller([string]$Installer, [string]$Target) {
    $arguments = @('/passive','InstallAllUsers=0','PrependPath=0','AppendPath=0',
        'Include_launcher=0','AssociateFiles=0','Shortcuts=0','Include_test=0',
        'Include_doc=0','Include_pip=1',"TargetDir=`"$Target`"")
    $process = Start-Process -FilePath $Installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -eq 1602) { throw 'Python installation was cancelled. You can rerun setup whenever you are ready.' }
    if ($process.ExitCode -notin @(0,3010)) { throw "Python installation failed (code $($process.ExitCode)). Retry setup or install Python 3.12 x64 from python.org." }
    if ($process.ExitCode -eq 3010) { Write-Host 'Python requests a restart. Restart Windows if the next check fails.' }
}

function Install-SupportedPython {
    if (-not (Confirm-Step 'No compatible Python was found. Download and install official Python 3.12 x64 for your Windows account? About 30 MB download and 150 MB installed; internet required. Existing Python installations, PATH and file associations will stay untouched.')) {
        throw 'Python installation was declined. No changes made. Rerun setup when ready, or install Python 3.12 x64 from python.org.'
    }
    $target = Join-Path $env:LOCALAPPDATA 'PerpetualBistro\Python312'
    if (Test-Path -LiteralPath $target) {
        throw "The dedicated Python folder already exists but could not be validated: $target. Setup will not overwrite it. Ask your group lead to repair that Python installation, then retry."
    }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('bistro-python-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        $installer = Join-Path $temporary 'python-installer.exe'
        Write-Host 'Downloading official Python installer...'
        Receive-OfficialPython $installer
        Write-Host 'Verifying Python installer signature...'
        Assert-PythonSignature $installer
        Write-Host 'Installing Python for your account...'
        Invoke-PythonInstaller $installer $target
        $probe = Invoke-Probe (Join-Path $target 'python.exe')
        if (-not (Test-Compatible $probe) -or $probe.minor -ne 12) { throw 'The installed Python could not be verified. Rerun setup after restarting Windows.' }
        return $probe
    } finally {
        # Only our unique download folder. Never touch Python installations or app data.
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Setup {
    Write-Host 'Checking Python installation...'
    $selected = Select-Python @(Get-PythonCandidates)
    if ($null -eq $selected) { $selected = Install-SupportedPython }
    Write-Host "Python 3.$($selected.minor) found! ($($selected.executable))"
    $arguments = @((Join-Path $script:ProjectRoot 'scripts\setup_environment.py'))
    if ($PullModel) { $arguments += '--pull-model' }
    & $selected.executable @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. Follow the message above, then double-click setup.bat again. Your restaurant records were preserved.' }
}

function Test-SetupReady {
    $exe = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Compatible (Invoke-Probe $exe) -AllowVenv)) { return $false }
    & $exe (Join-Path $script:ProjectRoot 'scripts\launch_application.py') --check-setup *> $null
    return $LASTEXITCODE -eq 0
}

function Test-WindowsPlatform {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { return $false }
    try {
        $build = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name CurrentBuildNumber
        return [int]$build -ge 10240
    } catch { return $false }
}

function Invoke-Application([string]$Executable,[object[]]$Arguments) {
    & $Executable @Arguments
    $script:ApplicationExitCode = $LASTEXITCODE
}

function Invoke-Bootstrap {
    if (-not (Test-WindowsPlatform)) {
        throw 'This installer supports Windows 10/11 on Intel/AMD x64 PCs. ARM64 and 32-bit Windows require a separate supported setup.'
    }
    Set-Location -LiteralPath $script:ProjectRoot
    if ($Mode -eq 'Setup') { Invoke-Setup; return }
    if (-not (Test-SetupReady)) {
        if (-not (Confirm-Step 'Application setup is missing, moved, or incomplete. Run setup now?')) {
            throw 'Setup was declined. Double-click setup.bat when ready.'
        }
        Invoke-Setup
        if (-not (Test-SetupReady)) { throw 'The application environment is not ready. Please rerun setup.bat.' }
    }
    $arguments = @((Join-Path $script:ProjectRoot 'scripts\launch_application.py'))
    if ($Check) { $arguments += '--check' }
    if ($NoBrowser) { $arguments += '--no-browser' }
    $exe = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
    Invoke-Application $exe $arguments
    if ($script:ApplicationExitCode -eq 2 -and -not $Check) {
        if (-not (Confirm-Step 'Ollama or the AI model is not ready. Run setup to repair prerequisites?')) {
            throw 'Prerequisite repair was declined. Double-click setup.bat when ready.'
        }
        Invoke-Setup
        Invoke-Application $exe $arguments
    }
    if ($script:ApplicationExitCode -ne 0) { throw 'Startup did not complete. Read the message above. Double-click setup.bat to repair prerequisites; existing orders are preserved.' }
}

if (-not $LibraryOnly) {
    try {
        foreach ($argument in $LegacyArgs) {
            switch ($argument) {
                '--check' { $Check = $true }
                '--no-browser' { $NoBrowser = $true }
                '--pull-model' { $PullModel = $true }
                default { throw "Unknown option: $argument" }
            }
        }
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:ProjectRoot.ToUpperInvariant()))).Replace('-','') }
        finally { $hash.Dispose() }
        $mutex = [Threading.Mutex]::new($false, "Local\PerpetualBistro-$digest")
        $owned = $false
        try {
            try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
            if (-not $owned) { throw 'Setup or the application is already running for this project. Return to its existing window; no other process was stopped.' }
            Invoke-Bootstrap
        } finally { if ($owned) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
        exit 0
    }
    catch { Write-Host "INSTALLATION/STARTUP STOPPED: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
}
