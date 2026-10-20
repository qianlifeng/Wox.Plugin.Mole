# Adapt the installed Mole Windows implementation; do not duplicate its
# registry scanning, installer launching or leftover-deletion policy.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
try {
    $request = $env:WOX_MOLE_REQUEST | ConvertFrom-Json
    if ($request.operation -notin @('list', 'uninstall')) { throw 'Invalid operation' }
    $entry = Join-Path $request.root 'bin\uninstall.ps1'
    $metadata = Get-Command -Name $entry -CommandType ExternalScript
    if (-not $metadata.Parameters.ContainsKey('ShowHelp')) {
        throw 'This Mole version cannot be loaded without its interactive menu. Update Mole.'
    }
    # Help loads Mole's functions without entering its interactive menu.
    . $entry -ShowHelp 1>$null 6>$null
    foreach ($name in @('Get-InstalledApplications', 'Get-AppSelectionKey', 'Uninstall-SelectedApps', 'Test-ProtectedApp')) {
        if (-not (Get-Command $name -CommandType Function -ErrorAction SilentlyContinue)) {
            throw "This Mole version does not provide $name. Update Mole."
        }
    }
    $apps = @(Get-InstalledApplications -ForceRescan:([bool]$request.refresh))
    if ($request.operation -eq 'uninstall') {
        # Resolve identity against a fresh Mole scan; never accept an installer
        # command or filesystem path supplied by the result row.
        $selected = @($apps | Where-Object { (Get-AppSelectionKey $_) -ceq $request.key })
        if ($selected.Count -ne 1) { throw 'The selected application changed or is no longer installed. Refresh the list.' }
        if ((Get-AppSelectionKey $selected[0]) -like 'name:*') { throw 'Mole did not provide a stable application identity.' }
        if (Test-ProtectedApp $selected[0].Name) { throw 'Mole protects this application from removal.' }
        Set-DryRunMode -Enabled $false
        Uninstall-SelectedApps -Apps $selected
        $apps = @(Get-InstalledApplications -ForceRescan)
        if (@($apps | Where-Object { (Get-AppSelectionKey $_) -ceq $request.key }).Count -gt 0) {
            throw 'The application is still listed by Mole. Uninstall may have been cancelled, failed, or require a restart.'
        }
    }
    $rows = @($apps | ForEach-Object {
        [PSCustomObject]@{
            key = Get-AppSelectionKey $_
            name = [string]$_.Name
            size = [string]$_.SizeHuman
            size_kb = [double]$_.SizeKB
        }
    })
    [Console]::WriteLine('__WOX_MOLE_RESULT__' + (ConvertTo-Json -InputObject $rows -Compress -Depth 4))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
