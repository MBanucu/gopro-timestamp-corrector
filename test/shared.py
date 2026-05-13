"""Shared test utilities."""

import tkinter as tk
from tkinter import ttk

from tzcombobox import FilteringCombobox


TEST_ZONES = [
    'EET', 'EST', 'Europe/Amsterdam', 'Europe/Berlin',
    'Europe/London', 'Europe/Paris', 'Europe/Rome',
    'Europe/Vienna', 'Europe/Zurich', 'Eire',
]


def make_cb(root):
    cb = FilteringCombobox(root, all_values=TEST_ZONES, width=30)
    cb.pack()
    root.update_idletasks()
    return cb
