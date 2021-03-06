import contextlib
import logging
import time

import numpy as np

import comet
# from comet.driver.keysight import E4980A
from comet.driver.keithley import K2657A
from ..driver import E4980A

from ..utils import format_metric
from ..estimate import Estimate
from ..benchmark import Benchmark

from .matrix import MatrixMeasurement
from .measurement import format_estimate
from .measurement import QUICK_RAMP_DELAY

from .mixins import VSourceMixin
from .mixins import LCRMixin
from .mixins import EnvironmentMixin
from .mixins import AnalysisMixin

__all__ = ["CVRampHVMeasurement"]

class CVRampHVMeasurement(MatrixMeasurement, VSourceMixin, LCRMixin, EnvironmentMixin, AnalysisMixin):
    """CV ramp measurement."""

    type = "cv_ramp_vsrc"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.register_parameter('bias_voltage_start', unit='V', required=True)
        self.register_parameter('bias_voltage_stop', unit='V', required=True)
        self.register_parameter('bias_voltage_step', unit='V', required=True)
        self.register_parameter('waiting_time', unit='s', required=True)
        self.register_parameter('vsrc_current_compliance', unit='A', required=True)
        self.register_vsource()
        self.register_lcr()
        self.register_environment()
        self.register_analysis()

    def quick_ramp_zero(self, vsrc):
        """Ramp to zero voltage without measuring current."""
        self.process.emit("message", "Ramp to zero...")
        self.process.emit("progress", 0, 1)

        bias_voltage_step = self.get_parameter('bias_voltage_step')

        vsrc_output_state = self.vsrc_get_output_state(vsrc)
        self.process.emit("state", dict(
            vsrc_output=vsrc_output_state
        ))
        if vsrc_output_state:
            vsrc_voltage_level = self.vsrc_get_voltage_level(vsrc)
            ramp = comet.Range(vsrc_voltage_level, 0, bias_voltage_step)
            for step, voltage in enumerate(ramp):
                self.process.emit("progress", step + 1, ramp.count)
                self.vsrc_set_voltage_level(vsrc, voltage)
                self.process.emit("state", dict(
                    vsrc_voltage=voltage
                ))
                time.sleep(QUICK_RAMP_DELAY)
        self.process.emit("state", dict(
            vsrc_output=self.vsrc_get_output_state(vsrc)
        ))
        self.process.emit("message", "")
        self.process.emit("progress", 1, 1)

    def initialize(self, vsrc, lcr):
        self.process.emit("progress", 1, 6)

        # Parameters
        bias_voltage_start = self.get_parameter('bias_voltage_start')
        bias_voltage_step = self.get_parameter('bias_voltage_step')
        bias_voltage_stop = self.get_parameter('bias_voltage_stop')
        waiting_time = self.get_parameter('waiting_time')
        vsrc_current_compliance = self.get_parameter('vsrc_current_compliance')

        # Extend meta data
        self.set_meta("bias_voltage_start", f"{bias_voltage_start:G} V")
        self.set_meta("bias_voltage_stop", f"{bias_voltage_stop:G} V")
        self.set_meta("bias_voltage_step", f"{bias_voltage_step:G} V")
        self.set_meta("waiting_time", f"{waiting_time:G} s")
        self.set_meta("vsrc_current_compliance", f"{vsrc_current_compliance:G} A")
        self.vsrc_update_meta()
        self.lcr_update_meta()
        self.environment_update_meta()

        # Series units
        self.set_series_unit("timestamp", "s")
        self.set_series_unit("voltage_vsrc", "V")
        self.set_series_unit("current_vsrc", "A")
        self.set_series_unit("capacitance", "F")
        self.set_series_unit("capacitance2", "1")
        self.set_series_unit("resistance", "Ohm")
        self.set_series_unit("temperature_box", "degC")
        self.set_series_unit("temperature_chuck", "degC")
        self.set_series_unit("humidity_box", "%")

        # Series
        self.register_series("timestamp")
        self.register_series("voltage_vsrc")
        self.register_series("current_vsrc")
        self.register_series("capacitance")
        self.register_series("capacitance2")
        self.register_series("resistance")
        self.register_series("temperature_box")
        self.register_series("temperature_chuck")
        self.register_series("humidity_box")

        # Initialize V Source

        self.process.emit("message", "Initialize...")
        self.process.emit("progress", 2, 6)

        self.vsrc_reset(vsrc)
        self.process.emit("progress", 3, 6)

        self.vsrc_setup(vsrc)
        self.vsrc_set_function_voltage(vsrc)
        self.vsrc_set_current_compliance(vsrc, vsrc_current_compliance)
        self.process.emit("progress", 4, 6)

        self.vsrc_set_output_state(vsrc, vsrc.OUTPUT_ON)
        vsrc_output_state = self.vsrc_get_output_state(vsrc)
        self.process.emit("state", dict(
            vsrc_output=vsrc_output_state,
        ))

        # Initialize LCR

        self.lcr_reset(lcr)
        self.process.emit("progress", 5, 6)

        self.lcr_setup(lcr)
        self.process.emit("progress", 6, 6)

    def measure(self, vsrc, lcr):
        self.process.emit("progress", 1, 2)
        # Parameters
        bias_voltage_start = self.get_parameter('bias_voltage_start')
        bias_voltage_step = self.get_parameter('bias_voltage_step')
        bias_voltage_stop = self.get_parameter('bias_voltage_stop')
        waiting_time = self.get_parameter('waiting_time')
        lcr_soft_filter = self.get_parameter('lcr_soft_filter')

        # Ramp to start voltage

        vsrc_voltage_level = self.vsrc_get_voltage_level(vsrc)

        logging.info("V Source ramp to start voltage: from %E V to %E V with step %E V", vsrc_voltage_level, bias_voltage_start, bias_voltage_step)
        for voltage in comet.Range(vsrc_voltage_level, bias_voltage_start, bias_voltage_step):
            self.process.emit("message", "Ramp to start... {}".format(format_metric(voltage, "V")))
            self.vsrc_set_voltage_level(vsrc, voltage)
            time.sleep(QUICK_RAMP_DELAY)
            self.process.emit("state", dict(
                vsrc_voltage=voltage,
            ))

            # Compliance tripped?
            self.vsrc_check_compliance(vsrc)

            if not self.process.running:
                break

        if not self.process.running:
            return


        vsrc_voltage_level = self.vsrc_get_voltage_level(vsrc)

        ramp = comet.Range(vsrc_voltage_level, bias_voltage_stop, bias_voltage_step)
        est = Estimate(ramp.count)
        self.process.emit("progress", *est.progress)

        t0 = time.time()

        self.vsrc_clear(vsrc)

        benchmark_step = Benchmark("Single_Step")
        benchmark_lcr = Benchmark("Read_LCR")
        benchmark_vsrc = Benchmark("Read_V_Source")
        benchmark_environ = Benchmark("Read_Environment")

        logging.info("V Source ramp to end voltage: from %E V to %E V with step %E V", vsrc_voltage_level, ramp.end, ramp.step)
        for voltage in ramp:
            with benchmark_step:
                self.vsrc_set_voltage_level(vsrc, voltage)

                # Delay
                time.sleep(waiting_time)

                # vsrc_voltage_level = self.vsrc_get_voltage_level(vsrc)
                dt = time.time() - t0
                est.advance()
                self.process.emit("message", "{} | V Source {}".format(format_estimate(est), format_metric(voltage, "V")))
                self.process.emit("progress", *est.progress)

                # read LCR, for CpRp -> prim: Cp, sec: Rp
                with benchmark_lcr:
                    try:
                        if lcr_soft_filter:
                            lcr_prim, lcr_sec = self.lcr_acquire_filter_reading(lcr)
                        else:
                            lcr_prim, lcr_sec = self.lcr_acquire_reading(lcr)
                    except Exception as exc:
                        raise RuntimeError(f"Failed to read from LCR: {exc}") from exc
                    try:
                        lcr_prim2 = 1.0 / (lcr_prim * lcr_prim)
                    except ZeroDivisionError:
                        lcr_prim2 = 0.0

                # read V Source
                with benchmark_vsrc:
                    vsrc_reading = self.vsrc_read_current(vsrc)

                self.process.emit("reading", "lcr", abs(voltage) if ramp.step < 0 else voltage, lcr_prim)
                self.process.emit("reading", "lcr2", abs(voltage) if ramp.step < 0 else voltage, lcr_prim2)

                self.process.emit("update")
                self.process.emit("state", dict(
                    vsrc_voltage=voltage,
                    vsrc_current=vsrc_reading
                ))

                self.environment_update()

                self.process.emit("state", dict(
                    env_chuck_temperature=self.environment_temperature_chuck,
                    env_box_temperature=self.environment_temperature_box,
                    env_box_humidity=self.environment_humidity_box
                ))

                # Append series data
                self.append_series(
                    timestamp=dt,
                    voltage_vsrc=voltage,
                    current_vsrc=vsrc_reading,
                    capacitance=lcr_prim,
                    capacitance2=lcr_prim2,
                    resistance=lcr_sec,
                    temperature_box=self.environment_temperature_box,
                    temperature_chuck=self.environment_temperature_chuck,
                    humidity_box=self.environment_humidity_box
                )

                # Compliance tripped?
                self.vsrc_check_compliance(vsrc)

                if not self.process.running:
                    break

        logging.info(benchmark_step)
        logging.info(benchmark_lcr)
        logging.info(benchmark_vsrc)
        logging.info(benchmark_environ)

    def analyze(self, **kwargs):
        self.process.emit("progress", 0, 1)

        v = np.array(self.get_series('voltage_vsrc'))
        c = np.array(self.get_series('capacitance'))

        if len(v) > 1 and len(c) > 1:

            for f in self.analysis_functions():
                r = f(v=v, c=c)
                logging.info(r)
                key, values = type(r).__name__, r._asdict()
                self.set_analysis(key, values)
                self.process.emit("append_analysis", key, values)
                if 'x_fit' in r._asdict():
                    for x, y in [(x, r.a * x + r.b) for x in r.x_fit]:
                        self.process.emit("reading", "xfit", x, y)
                    self.process.emit("update")

        self.process.emit("progress", 1, 1)

    def finalize(self, vsrc, lcr):
        self.process.emit("progress", 1, 2)
        self.process.emit("state", dict(
            vsrc_current=None,
        ))

        self.quick_ramp_zero(vsrc)
        self.vsrc_set_output_state(vsrc, vsrc.OUTPUT_OFF)
        self.process.emit("state", dict(
            vsrc_output=self.vsrc_get_output_state(vsrc),
        ))

        self.process.emit("state", dict(
            env_chuck_temperature=None,
            env_box_temperature=None,
            env_box_humidity=None
        ))

        self.process.emit("progress", 2, 2)

    def run(self):
        with contextlib.ExitStack() as es:
            super().run(
                vsrc=self.vsrc_create(es.enter_context(self.resources.get("vsrc"))),
                lcr=E4980A(es.enter_context(self.resources.get("lcr")))
            )
