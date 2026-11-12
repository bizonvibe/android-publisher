param([switch]$Live, [switch]$Preview)
$ErrorActionPreference = 'Stop'
$PublisherRoot = Split-Path $PSScriptRoot -Parent
Set-Location $PublisherRoot
$PublisherMode = if ($Live) { 'LIVE' } else { 'DRY_RUN' }
if ($Live -and -not $Preview) {
    $PublisherPolicyPath = Join-Path $PublisherRoot 'data\daily-authorization.json'
    if (-not (Test-Path $PublisherPolicyPath)) { throw 'Run the verified LIVE pilot and enable-daily first.' }
    $PublisherPolicy = Get-Content $PublisherPolicyPath -Raw | ConvertFrom-Json
    if ($PublisherPolicy.enabled -ne $true -or $PublisherPolicy.account_id -ne 'crabscoder') {
        throw 'Daily publication has not been authorized for crabscoder.'
    }
}
# Secrets stay in ENV. No secret is written into the task XML, arguments or project files.
if (-not $Preview) {
    foreach ($PublisherName in @('PUBLISHER_R2_ACCESS_KEY_ID','PUBLISHER_R2_SECRET_ACCESS_KEY')) {
        $PublisherValue = [Environment]::GetEnvironmentVariable($PublisherName, 'User')
        if (-not $PublisherValue) {
            $PublisherSecure = Read-Host "$PublisherName (stored in this Windows user's environment)" -AsSecureString
            $PublisherPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PublisherSecure)
            try {
                $PublisherValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PublisherPointer)
                if (-not $PublisherValue) { throw 'R2 credential cannot be empty.' }
                [Environment]::SetEnvironmentVariable($PublisherName, $PublisherValue, 'User')
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PublisherPointer)
            }
        }
    }
}
$PublisherOwner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$PublisherRunScript = Join-Path $PSScriptRoot 'Run-DailyPublisher.ps1'
$PublisherEscape = { param($Value) [Security.SecurityElement]::Escape($Value) }
$PublisherOwnerXml = & $PublisherEscape $PublisherOwner
$PublisherRootXml = & $PublisherEscape $PublisherRoot
$PublisherArguments = & $PublisherEscape ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PublisherRunScript + '" -ExecutionMode ' + $PublisherMode)
$PublisherMoscow = [TimeZoneInfo]::FindSystemTimeZoneById('Russian Standard Time')
$PublisherMoscowNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $PublisherMoscow)
$PublisherNext = $PublisherMoscowNow.Date.AddHours(12)
if ($PublisherNext -le $PublisherMoscowNow) { $PublisherNext = $PublisherNext.AddDays(1) }
$PublisherDate = $PublisherNext.ToString('yyyy-MM-dd')
# Offset is explicit: noon Moscow, regardless of this PC's timezone.
$PublisherBoundary = $PublisherDate + 'T12:00:00+03:00'
$PublisherXml = @"
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Check ready R2 daily Reels and publish new NORMAL_REEL to crabscoder. $PublisherMode.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$PublisherBoundary</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Owner"><UserId>$PublisherOwnerXml</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable><Enabled>true</Enabled><WakeToRun>true</WakeToRun><ExecutionTimeLimit>PT2H</ExecutionTimeLimit></Settings>
  <Actions Context="Owner"><Exec><Command>powershell.exe</Command><Arguments>$PublisherArguments</Arguments><WorkingDirectory>$PublisherRootXml</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Preview) { Write-Output $PublisherXml; exit 0 }
Register-ScheduledTask -TaskName 'Crabscoder Android Reels Publisher' -Xml $PublisherXml -Force | Out-Null
Write-Host "Installed: every day at 12:00 Europe/Moscow; mode=$PublisherMode. PC must be on, user logged in, Android unlocked and connected by USB."
Write-Host 'Logs: data\schedule-logs. Task does not run immediately.'
