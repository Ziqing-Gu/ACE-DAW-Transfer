"""Exercise the live bilingual controls without starting a conversion."""
import unittest
from unittest.mock import patch

from tkinterdnd2 import TkinterDnD

import app
import gui_preferences


class WindowLanguageTests(unittest.TestCase):
    def test_switch_and_reopen_with_the_same_choices(self):
        preferences = dict(bake_fades=True, static_only=False, presets_only=True, output_format="npr")

        def exercise(initial, switch_to):
            window = TkinterDnD.Tk()
            window.withdraw()

            def interact():
                frame = window.winfo_children()[0]
                def at(row):
                    return frame.grid_slaves(row=row, column=0)[0]

                picker = frame.children["language"]
                output_format = at(2).grid_slaves(row=0, column=1)[0]
                convert = at(11).grid_slaves(row=0, column=0)[0]
                source, folder = at(4), at(6)
                self.assertEqual(picker.get(), initial)
                self.assertEqual(str(convert.cget("text")), "Convert" if initial == "English" else "开始转换")
                source.insert(0, "Cue 03.acep")
                folder.delete(0, "end")
                folder.insert(0, "Cue prints")

                picker.set(switch_to)
                picker.event_generate("<<ComboboxSelected>>")
                english = switch_to == "English"
                self.assertEqual(str(convert.cget("text")), "Convert" if english else "开始转换")
                self.assertEqual(output_format.get(), "Nuendo project (.npr)" if english else "Nuendo 工程 (.npr)")
                hint = at(2).grid_slaves(row=1, column=1)[0]
                self.assertIn("directly" if english else "直接", window.getvar(str(hint.cget("textvariable"))))
                self.assertEqual((source.get(), folder.get()), ("Cue 03.acep", "Cue prints"))
                self.assertEqual([at(r).instate(["selected"]) for r in (8, 9, 10)], [True, False, True])
                self.assertEqual(preferences["language"], "en" if english else "zh")
                for callback in window.tk.call("after", "info"):
                    window.after_cancel(callback)

            try:
                with patch.object(TkinterDnD, "Tk", return_value=window), patch.object(window, "mainloop", interact):
                    app.gui()
            finally:
                window.destroy()

        with patch.object(gui_preferences, "load", side_effect=preferences.copy), \
             patch.object(gui_preferences, "save", side_effect=preferences.__setitem__), \
             patch.object(app, "transfer") as transfer:
            exercise("中文", "English")
            exercise("English", "中文")
            transfer.assert_not_called()

