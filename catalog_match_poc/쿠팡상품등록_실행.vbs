Option Explicit

Dim shell, fileSystem, baseDirectory, pythonWindow, mainScript, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
baseDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonWindow = fileSystem.BuildPath(baseDirectory, ".venv\Scripts\pythonw.exe")
mainScript = fileSystem.BuildPath(baseDirectory, "main.py")

If Not fileSystem.FileExists(pythonWindow) Then
    MsgBox "Please run the setup file first.", 48, "Coupang Registration Helper"
    WScript.Quit 1
End If

shell.CurrentDirectory = baseDirectory
command = Chr(34) & pythonWindow & Chr(34) & " " & Chr(34) & mainScript & Chr(34)
shell.Run command, 0, False
