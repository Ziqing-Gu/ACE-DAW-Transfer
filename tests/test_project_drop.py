import json
import sys
import tempfile
import unittest
from pathlib import Path
from tkinter import filedialog
from unittest.mock import patch

import app


@unittest.skipUnless(sys.platform == 'win32', 'Windows Explorer file drops')
class ProjectDropTests(unittest.TestCase):
    def test_file_inputs_and_windows_drop_events(self):
        from tkinterdnd2 import TkinterDnD

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / '配乐 {镜头 2}'
            folder.mkdir()
            cpr, npr, acep, xml, other = [
                folder / name for name in ('混音.CPR', '对白.npr', '配乐.acep', '声部.xml', '资料.txt')]
            for path in (cpr, npr, acep, xml, other):
                path.touch()

            window = TkinterDnD.Tk()
            window.withdraw()
            self.addCleanup(window.destroy)

            def exercise():
                frame = window.winfo_children()[0]
                def at(row):
                    return frame.grid_slaves(row=row, column=0)[0]
                source, output = at(4), at(6)
                choice = at(2).grid_slaves(row=0, column=1)[0]

                def drop(over, *files):
                    call = window.tk.call
                    widget = str(over)
                    data = tuple(path.as_posix() for path in files)
                    call('tkdnd::olednd::HandleDragEnter', widget, ('CF_HDROP',), ('copy',), '', 0, 0, (15,))
                    try:
                        return call('tkdnd::olednd::HandleDrop', widget, '', 0, 0, 'CF_HDROP', data)
                    finally:
                        call('tkdnd::olednd::HandleDragLeave', widget)

                # Explorer also supplies a positional argument to the executable/shortcut.
                self.assertEqual(source.get(), str(acep))
                self.assertEqual(output.get(), str(folder))
                self.assertEqual(app.TARGETS[choice.get()], 'xml')
                for over, file in [(source, cpr), (at(12), npr), (output, xml), (frame, acep)]:
                    self.assertEqual(drop(over, file), 'copy')
                    self.assertEqual(source.get(), str(file))
                    self.assertEqual(app.TARGETS[choice.get()], 'xml' if file == acep else 'acep')
                with patch.object(filedialog, 'askdirectory', return_value=temporary):
                    frame.grid_slaves(row=6, column=1)[0].invoke()
                for invalid in [(cpr, npr), (other,), (folder,), (folder/'missing.npr',)]:
                    self.assertEqual(drop(window, *invalid), 'refuse_drop')
                    self.assertEqual(source.get(), str(acep))
                    self.assertEqual(output.get(), temporary)
                self.assertEqual([app.TARGETS[label] for label in choice['values']],['xml','npr','cpr'])
                (folder/'transfer-map.json').write_text(json.dumps({'schema': 2, 'source_archive': str(npr),
                    'source_sha256':app.sha(npr),'tracks':[],'events':[]}))
                self.assertEqual(drop(frame, acep), 'copy')
                self.assertEqual(app.TARGETS[choice.get()], 'npr')
                self.assertEqual([app.TARGETS[label] for label in choice['values']],['npr','cpr','xml'])
                self.assertEqual(output.get(), str(folder))
                with patch.object(filedialog, 'askopenfilename', return_value=str(cpr)):
                    frame.grid_slaves(row=4, column=1)[0].invoke()
                self.assertEqual(source.get(), str(cpr))
                self.assertEqual(app.TARGETS[choice.get()], 'acep')

            with patch.object(TkinterDnD, 'Tk', return_value=window), patch.object(window, 'mainloop', exercise), \
                    patch('gui_preferences.load',return_value=dict(bake_fades=False,static_only=False,presets_only=False)), \
                    patch.object(app, 'transfer') as convert:
                app.gui([str(acep)])
                convert.assert_not_called()

    def test_window_restores_each_choice_on_reopen_without_converting(self):
        import uuid
        import winreg
        import gui_preferences
        from tkinterdnd2 import TkinterDnD

        key=r'Software\ACE-DAW-Test-'+uuid.uuid4().hex
        def remove_test_key():
            try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
            except FileNotFoundError:pass
        self.addCleanup(remove_test_key)

        def open_window(before, rows, initial_target, new_target=None):
            window=TkinterDnD.Tk()
            window.withdraw()
            def exercise():
                frame=window.winfo_children()[0]
                checks=[frame.grid_slaves(row=r,column=0)[0] for r in (8,9,10)]
                self.assertEqual([bool(c.instate(['selected'])) for c in checks],before)
                for i in rows:checks[i].invoke()
                choice=frame.grid_slaves(row=2,column=0)[0].grid_slaves(row=0,column=1)[0]
                self.assertEqual(app.TARGETS[choice.get()],initial_target)
                if new_target:
                    choice.set(next(label for label,fmt in app.TARGETS.items() if fmt==new_target))
                    choice.event_generate('<<ComboboxSelected>>')
            try:
                with patch.object(TkinterDnD,'Tk',return_value=window),patch.object(window,'mainloop',exercise):
                    app.gui()
            finally:window.destroy()

        with patch.object(gui_preferences,'KEY',key),patch.object(app,'transfer') as convert:
            open_window([False,False,False],[0,2],'xml','npr')
            open_window([True,False,True],[0,1],'npr')
            open_window([False,True,True],[1,2],'npr')
            open_window([False,False,False],[],'npr')
            convert.assert_not_called()
