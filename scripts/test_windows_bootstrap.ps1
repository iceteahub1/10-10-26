# Dependency-free simulated tests. No real registry/download/install is used.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\windows_bootstrap.ps1" -LibraryOnly
$script:Checks = 0
function Assert-True($Condition,[string]$Label) {
    if (-not $Condition) { throw "FAILED: $Label" }
    $script:Checks++
    Write-Host "PASS: $Label"
}
function Assert-Throws([scriptblock]$Action,[string]$Pattern,[string]$Label) {
    $failed = $false
    try { & $Action | Out-Null } catch { $failed = $_.Exception.Message -match $Pattern }
    Assert-True $failed $Label
}
# Same legacy options passed by batch entry points are bound as remaining args.
. "$PSScriptRoot\windows_bootstrap.ps1" -Mode Launch -LibraryOnly --check --no-browser
Assert-True ($LegacyArgs.Count -eq 2 -and $LegacyArgs[0] -eq '--check') 'legacy run.bat option binding'
$script:Probes = @{}
$script:ProbeCalls = @()
function Invoke-Probe([string]$Executable,[string]$Prefix='') {
    $script:ProbeCalls += "$Executable|$Prefix"
    return $script:Probes["$Executable|$Prefix"]
}
function Test-Path { param($LiteralPath,$PathType) return $true }
function New-Probe($Version,$Bits=64) {
    return [pscustomobject]@{executable="python$Version.exe";major=3;minor=$Version;micro=10;bits=$Bits;implementation='cpython';base=$true}
}
$script:Probes['313.exe|'] = New-Probe 13
$script:Probes['311.exe|'] = New-Probe 11
$script:Probes['312.exe|'] = New-Probe 12
$script:Probes['32bit.exe|'] = New-Probe 12 32
function Candidate($Exe,$Prefix='') { return [pscustomobject]@{Executable=$Exe;Prefix=$Prefix} }
Assert-True ($null -eq (Select-Python @())) 'missing Python / missing py launcher'
Assert-True ((Select-Python @((Candidate '313.exe'),(Candidate '311.exe'),(Candidate '312.exe'))).minor -eq 12) 'multiple versions: prefer 3.12, reject 3.13 first in PATH'
Assert-True ((Select-Python @((Candidate '313.exe'),(Candidate '311.exe'))).minor -eq 11) '3.11 without py launcher'
Assert-True ($null -eq (Select-Python @((Candidate '32bit.exe')))) 'reject 32-bit interpreter'
$script:Probes['py.exe|-3.12'] = New-Probe 12
Assert-True ((Select-Python @((Candidate 'py.exe' '-3.12'))).minor -eq 12) 'verify launcher-selected interpreter'
$script:ProbeCalls = @()
$null = Select-Python @((Candidate 'C:\Users\User\WindowsApps\python.exe'))
Assert-True ($script:ProbeCalls.Count -eq 0) 'skip Microsoft Store execution alias'

# Replace platform/external functions with fixtures; use real bootstrap decision flow.
Remove-Item Function:\Test-Path
$script:TemporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ('bistro-tests-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $script:TemporaryRoot | Out-Null
$oldLocal = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $script:TemporaryRoot
$script:Allowed = $false
$script:Downloaded = 0; $script:Installed = 0; $script:SignatureChecked = 0
function Confirm-Step([string]$Message) { return $script:Allowed }
function Receive-OfficialPython([string]$Destination) { $script:Downloaded++; throw 'simulated failed download' }
function Assert-PythonSignature([string]$Installer) { $script:SignatureChecked++ }
function Invoke-PythonInstaller([string]$Installer,[string]$Target) { $script:Installed++ }
try {
    Assert-Throws { Install-SupportedPython } 'declined' 'cancelled permission request'
    Assert-True ($script:Downloaded -eq 0 -and $script:Installed -eq 0) 'declined install has no external effects'
    $script:Allowed = $true
    Assert-Throws { Install-SupportedPython } 'failed download' 'failed download stops installation'
    Assert-True ($script:Installed -eq 0 -and $script:SignatureChecked -eq 0) 'download failure never executes installer'
    function Receive-OfficialPython([string]$Destination) { $script:Downloaded++ }
    function Assert-PythonSignature([string]$Installer) { throw 'simulated signature verification failed' }
    Assert-Throws { Install-SupportedPython } 'signature verification failed' 'bad signature stops installation'
    Assert-True ($script:Installed -eq 0) 'bad signature never executes installer'
    # Real signature decision, fake Windows signature provider.
    . "$PSScriptRoot\windows_bootstrap.ps1" -LibraryOnly
    function Get-AuthenticodeSignature {
        param($LiteralPath)
        $cert = [pscustomobject]@{Publisher=$script:Publisher}
        $cert | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($Type,$Issuer); return $this.Publisher }
        return [pscustomobject]@{Status=$script:SignatureStatus;SignerCertificate=$cert}
    }
    $script:SignatureStatus = 'Valid'; $script:Publisher = 'Untrusted Company'
    Assert-Throws { Assert-PythonSignature 'fixture.exe' } 'publisher verification failed' 'wrong signed publisher rejected'
    $script:Publisher = 'Python Software Foundation'; $script:SignatureStatus = 'HashMismatch'
    Assert-Throws { Assert-PythonSignature 'fixture.exe' } 'verification failed' 'modified installer rejected'
    $script:SignatureStatus = 'Valid'
    Assert-PythonSignature 'fixture.exe'
    Assert-True $true 'trusted signature and expected publisher accepted (simulated)'
    function Start-Process { param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru); $script:InstallerArguments = $ArgumentList; return [pscustomobject]@{ExitCode=$script:InstallerCode} }
    $script:InstallerCode = 1602
    Assert-Throws { Invoke-PythonInstaller 'fixture.exe' 'C:\fixture' } 'cancelled' 'installer cancellation handled'
    $script:InstallerCode = 1603
    Assert-Throws { Invoke-PythonInstaller 'fixture.exe' 'C:\fixture' } 'failed' 'installer failure handled'
    $script:InstallerCode = 0
    Invoke-PythonInstaller 'fixture.exe' 'C:\fixture'
    Assert-True $true 'installer success (simulated)'
    Assert-True ($script:InstallerArguments -contains 'PrependPath=0' -and $script:InstallerArguments -contains 'Include_launcher=0' -and $script:InstallerArguments -contains 'InstallAllUsers=0' -and $script:InstallerArguments -contains 'AssociateFiles=0') 'installer keeps PATH, launcher and associations unchanged; per-user only'
    # Real bootstrap startup routing with simulated readiness and child processes.
    function Test-WindowsPlatform { return $true }
    $script:ProjectRoot = $script:TemporaryRoot
    $Mode = 'Launch'; $Check = $false; $NoBrowser = $false
    $script:Ready = $false; $script:SetupCalls = 0; $script:LaunchCalls = 0; $script:Allowed = $false
    function Test-SetupReady { return $script:Ready }
    function Confirm-Step([string]$Message) { return $script:Allowed }
    function Invoke-Setup { $script:SetupCalls++; $script:Ready = $true }
    function Invoke-Application([string]$Executable,[object[]]$Arguments) {
        $script:LaunchCalls++
        $script:ApplicationExitCode = 0
    }
    Assert-Throws { Invoke-Bootstrap } 'declined' 'missing setup offers repair and respects decline'
    Assert-True ($script:SetupCalls -eq 0 -and $script:LaunchCalls -eq 0) 'declined startup repair launches nothing'
    $script:Allowed = $true
    Invoke-Bootstrap
    Assert-True ($script:SetupCalls -eq 1 -and $script:LaunchCalls -eq 1) 'approved startup repair then launches once'
    $script:SetupCalls = 0; $script:LaunchCalls = 0
    Invoke-Bootstrap
    Assert-True ($script:SetupCalls -eq 0 -and $script:LaunchCalls -eq 1) 'complete setup starts without reinstalling'
    $script:LaunchCalls = 0
    function Invoke-Application([string]$Executable,[object[]]$Arguments) {
        $script:LaunchCalls++
        $script:ApplicationExitCode = if ($script:LaunchCalls -eq 1) { 2 } else { 0 }
    }
    Invoke-Bootstrap
    Assert-True ($script:SetupCalls -eq 1 -and $script:LaunchCalls -eq 2) 'missing Ollama/model offers one repair then one retry'
} finally {
    $env:LOCALAPPDATA = $oldLocal
    Set-Location -LiteralPath $PSScriptRoot
    Remove-Item -LiteralPath $script:TemporaryRoot -Recurse -Force
}
Write-Host "$script:Checks simulated bootstrap checks passed. This does not prove real Windows installation."
