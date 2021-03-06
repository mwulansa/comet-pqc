import logging
import re

import comet
from comet import ui

from ..utils import format_metric
from .matrix import MatrixPanel
from .mixins import HVSourceMixin
from .mixins import EnvironmentMixin

__all__ = ["IVRampPanel"]

class IVRampPanel(MatrixPanel, HVSourceMixin, EnvironmentMixin):
    """Panel for IV ramp measurements."""

    type = "iv_ramp"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "IV Ramp"

        self.register_vsource()
        self.register_environment()

        self.plot = ui.Plot(height=300, legend="right")
        self.plot.add_axis("x", align="bottom", text="Voltage [V] (abs)")
        self.plot.add_axis("y", align="right", text="Current [uA]")
        self.plot.add_series("hvsrc", "x", "y", text="HV Source", color="red")
        self.plot.add_series("xfit", "x", "y", text="Fit", color="magenta")
        self.data_tabs.insert(0, ui.Tab(title="IV Curve", layout=self.plot))

        self.voltage_start = ui.Number(decimals=3, suffix="V")
        self.voltage_stop = ui.Number(decimals=3, suffix="V")
        self.voltage_step = ui.Number(minimum=0, maximum=200, decimals=3, suffix="V")
        self.waiting_time = ui.Number(minimum=0, decimals=2, suffix="s")

        self.hvsrc_current_compliance = ui.Metric(minimum=0, decimals=3, prefixes='mun', unit="A")

        self.bind("voltage_start", self.voltage_start, 0, unit="V")
        self.bind("voltage_stop", self.voltage_stop, 100, unit="V")
        self.bind("voltage_step", self.voltage_step, 1, unit="V")
        self.bind("waiting_time", self.waiting_time, 1, unit="s")

        self.bind("hvsrc_current_compliance", self.hvsrc_current_compliance, 0, unit="A")

        self.general_tab.layout = ui.Row(
            ui.GroupBox(
                title="Ramp",
                layout=ui.Column(
                    ui.Label(text="Start"),
                    self.voltage_start,
                    ui.Label(text="Stop"),
                    self.voltage_stop,
                    ui.Label(text="Step"),
                    self.voltage_step,
                    ui.Label(text="Waiting Time"),
                    self.waiting_time,
                    ui.Spacer()
                )
            ),
            ui.GroupBox(
                title="HV Source",
                layout=ui.Column(
                    ui.Label(text="Compliance"),
                    self.hvsrc_current_compliance,
                    ui.Spacer()
                )
            ),
            ui.Spacer(),
            stretch=(1, 1, 1)
        )

        ampere = comet.ureg('A')
        volt = comet.ureg('V')

        self.series_transform['hvsrc'] = lambda x, y: ((x * volt).to('V').m, (y * ampere).to('uA').m)
        self.series_transform['xfit'] = self.series_transform.get('hvsrc')

    def mount(self, measurement):
        super().mount(measurement)
        for name, points in measurement.series.items():
            if name in self.plot.series:
                self.plot.series.clear()
            tr = self.series_transform.get(name, self.series_transform_default)
            for x, y in points:
                self.plot.series.get(name).append(*tr(x, y))
        self.update_readings()

    def append_reading(self, name, x, y):
        if self.measurement:
            if name in self.plot.series:
                if name not in self.measurement.series:
                    self.measurement.series[name] = []
                self.measurement.series[name].append((x, y))
                tr = self.series_transform.get(name, self.series_transform_default)
                self.plot.series.get(name).append(*tr(x, y))
                self.plot.series.get(name).qt.setVisible(True)

    def update_readings(self):
        if self.measurement:
            if self.plot.zoomed:
                self.plot.update("x")
            else:
                self.plot.fit()

    def clear_readings(self):
        super().clear_readings()
        self.plot.series.get("xfit").qt.setVisible(False)
        for series in self.plot.series.values():
            series.clear()
        if self.measurement:
            for name, points in self.measurement.series.items():
                self.measurement.series[name] = []
        self.plot.fit()
