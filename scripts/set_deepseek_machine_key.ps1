param()

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$($MyInvocation.MyCommand.Path)`""
    )
    exit
}

$secureKey = Read-Host "请输入 DeepSeek API Key（输入内容不会显示）" -AsSecureString
if ($secureKey.Length -eq 0) {
    throw "API Key 不能为空。"
}

$apiKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
try {
    [Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", $apiKey, "Machine")
    $env:DEEPSEEK_API_KEY = $apiKey
    Write-Host "DEEPSEEK_API_KEY 已保存为系统级环境变量。重启 Codex 后即可读取。"
}
finally {
    $apiKey = $null
    $secureKey.Dispose()
}
