' Starts the Streamdeck companion without a window; output goes to companion\companion.log.
' Put a shortcut to this file in the Startup folder (Win+R, type shell:startup) to start it with Windows.
' To stop it: Task Manager -> end the "python.exe" that runs streamdeck_companion (or run Stop-Streamdeck.bat).
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """"" & folder & "\Streamdeck.bat"" > """ & folder & "\companion\companion.log"" 2>&1""", 0, False
