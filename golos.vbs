' Голос — запуск без окна консоли.
'
' Двойной щелчок по этому файлу = обычный запуск программы (как у любой
' программы с иконкой). Консоль не мелькает: WScript запускает pythonw.exe.
'
' run.bat остаётся для первого запуска и диагностики: он показывает прогресс
' установки зависимостей и ошибки, если что-то не так.

Option Explicit

Dim fso, shell, root, pythonw, script, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

pythonw = root & "\.venv\Scripts\pythonw.exe"
script = root & "\golos_app.py"

If Not fso.FileExists(pythonw) Then
    ' Окружения ещё нет — значит, программа не установлена. Показываем
    ' установку в консоли, иначе человек увидит «ничего не произошло».
    shell.Run "cmd /c """ & root & "\run.bat""", 1, False
    WScript.Quit 0
End If

command = """" & pythonw & """ """ & script & """"
shell.Run command, 0, False
