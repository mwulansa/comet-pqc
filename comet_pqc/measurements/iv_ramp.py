import datetime
import logging
import math
import time
import os
import re

import comet
from ..driver import K2410

from ..proxy import create_proxy
from ..formatter import PQCFormatter
from ..estimate import Estimate
from .matrix import MatrixMeasurement

__all__ = ["IVRampMeasurement"]

class IVRampMeasurement(MatrixMeasurement):
    """IV ramp measurement.

    * set compliance
    * if output enabled brings source voltage to zero
    * ramps to start voltage
    * ramps to end voltage
    * ramps back to zero

    In case of compliance, stop requests or errors ramps to zero before exit.
    """

    type = "iv_ramp"

    def env_detect_model(self, env):
        try:
            env_idn = env.resource.query("*IDN?")
        except Exception as e:
            raise RuntimeError("Failed to access Environment Box", env.resource.resource_name, e)
        logging.info("Detected Environment Box: %s", env_idn)
        # TODO
        self.process.emit("state", dict(
            env_model=env_idn
        ))

    def initialize(self, smu):
        self.process.emit("message", "Initialize...")
        self.process.emit("progress", 0, 5)

        parameters = self.measurement_item.parameters
        current_compliance = parameters.get("current_compliance").to("A").m
        voltage_start = parameters.get("voltage_start").to("V").m
        voltage_step = parameters.get("voltage_step").to("V").m
        waiting_time = parameters.get("waiting_time").to("s").m
        sense_mode = parameters.get("sense_mode")
        route_termination = parameters.get("route_termination", "rear")
        smu_filter_enable = bool(parameters.get("smu_filter_enable", False))
        smu_filter_count = int(parameters.get("smu_filter_count", 10))
        smu_filter_type = parameters.get("smu_filter_type", "repeat")

        smu_proxy = create_proxy(smu)

        smu_idn = smu_proxy.identification
        logging.info("Detected SMU: %s", smu_idn)
        result = re.search(r'model\s+([\d\w]+)', smu_idn, re.IGNORECASE).groups()
        smu_model = ''.join(result) or None

        if self.process.get("use_environ"):
            with self.resources.get("environ") as environ:
                self.env_detect_model(environ)

        self.process.emit("state", dict(
            smu_model=smu_model,
            smu_voltage=smu_proxy.source_voltage_level,
            smu_current=None,
            smu_output=smu_proxy.output_enable
        ))

        smu_proxy.reset()
        smu_proxy.assert_success()
        smu_proxy.clear()
        smu_proxy.assert_success()

        # Beeper off
        smu_proxy.beeper_enable = False
        smu_proxy.assert_success()
        self.process.emit("progress", 1, 5)

        # Select rear terminal
        if route_termination == "front":
            smu.resource.write(":ROUT:TERM FRONT")
        elif route_termination == "rear":
            smu.resource.write(":ROUT:TERM REAR")
        smu.resource.query("*OPC?")
        smu_proxy.assert_success()
        self.process.emit("progress", 2, 5)

        # set sense mode
        logging.info("set sense mode: '%s'", sense_mode)
        if sense_mode == "remote":
            smu.resource.write(":SYST:RSEN ON")
        elif sense_mode == "local":
            smu.resource.write(":SYST:RSEN OFF")
        else:
            raise ValueError(f"invalid sense mode: {sense_mode}")
        smu.resource.query("*OPC?")
        smu_proxy.assert_success()
        self.process.emit("progress", 3, 5)

        # Compliance
        logging.info("set compliance: %E A", current_compliance)
        smu.sense.current.protection.level = current_compliance
        smu_proxy.assert_success()

        # Range
        current_range = 1.05E-6
        smu.resource.write(":SENS:CURR:RANG:AUTO ON")
        smu.resource.query("*OPC?")
        smu_proxy.assert_success()
        #smu.resource.write(f":SENS:CURR:RANG {current_range:E}")
        #smu.resource.query("*OPC?")
        #smu_proxy.assert_success()

        # Filter

        smu_proxy.filter_count = smu_filter_count
        smu_proxy.assert_success()
        smu_proxy.filter_type = smu_filter_type.upper()
        smu_proxy.assert_success()
        smu_proxy.filter_enable = smu_filter_enable
        smu_proxy.assert_success()

        self.process.emit("progress", 5, 5)

        # If output enabled
        if smu_proxy.output_enable:
            voltage = smu_proxy.source_voltage_level

            logging.info("ramp to zero: from %E V to %E V with step %E V", voltage, 0, voltage_step)
            for voltage in comet.Range(voltage, 0, voltage_step):
                logging.info("set voltage: %E V", voltage)
                self.process.emit("message", f"{voltage:.3f} V")
                smu_proxy.source_voltage_level = voltage
                # smu_proxy.assert_success()
                time.sleep(.100)
                if not self.process.running:
                    break
        # If output disabled
        else:
            voltage = 0
            smu_proxy.source_voltage_level = voltage
            smu_proxy.assert_success()
            smu_proxy.output_enable = True
            smu_proxy.assert_success()
            time.sleep(.100)

        self.process.emit("progress", 2, 5)

        if self.process.running:

            voltage = smu_proxy.source_voltage_level

            # Get configured READ/FETCh elements
            elements = list(map(str.strip, smu.resource.query(":FORM:ELEM?").split(",")))
            smu_proxy.assert_success()

            logging.info("ramp to start voltage: from %E V to %E V with step %E V", voltage, voltage_start, voltage_step)
            for voltage in comet.Range(voltage, voltage_start, voltage_step):
                logging.info("set voltage: %E V", voltage)
                self.process.emit("message", f"{voltage:.3f} V")
                smu_proxy.source_voltage_level = voltage
                # smu_proxy.assert_success()
                time.sleep(.100)
                # Returns <elements> comma separated
                #values = list(map(float, smu.resource.query(":READ?").split(",")))
                #data = zip(elements, values)
                time.sleep(waiting_time)
                # Compliance?
                compliance_tripped = smu.sense.current.protection.tripped
                if compliance_tripped:
                    logging.error("SMU in compliance")
                    raise ValueError("compliance tripped")
                if not self.process.running:
                    break

        self.process.emit("progress", 5, 5)

    def measure(self, smu):
        sample_name = self.sample_name
        sample_type = self.sample_type
        output_dir = self.output_dir
        contact_name = self.measurement_item.contact.name
        measurement_name = self.measurement_item.name
        parameters = self.measurement_item.parameters
        current_compliance = parameters.get("current_compliance").to("A").m
        voltage_start = parameters.get("voltage_start").to("V").m
        voltage_step = parameters.get("voltage_step").to("V").m
        voltage_stop = parameters.get("voltage_stop").to("V").m
        waiting_time = parameters.get("waiting_time").to("s").m

        smu_proxy = create_proxy(smu)

        if not self.process.running:
            return

        iso_timestamp = comet.make_iso()
        filename = comet.safe_filename(f"{iso_timestamp}-{sample_name}-{sample_type}-{contact_name}-{measurement_name}.txt")
        with open(os.path.join(output_dir, filename), "w", newline="") as f:
            # Create formatter
            fmt = PQCFormatter(f)
            fmt.add_column("timestamp", ".3f")
            fmt.add_column("voltage", "E")
            fmt.add_column("current", "E")
            fmt.add_column("temperature_box", "E")
            fmt.add_column("temperature_chuck", "E")
            fmt.add_column("humidity_box", "E")

            # Write meta data
            fmt.write_meta("measurement_name", measurement_name)
            fmt.write_meta("measurement_type", self.type)
            fmt.write_meta("contact_name", contact_name)
            fmt.write_meta("sample_name", sample_name)
            fmt.write_meta("sample_type", sample_type)
            fmt.write_meta("start_timestamp", datetime.datetime.now(), "%Y-%m-%d %H:%M:%S")
            fmt.write_meta("voltage_start", f"{voltage_start:E} V")
            fmt.write_meta("voltage_stop", f"{voltage_stop:E} V")
            fmt.write_meta("voltage_step", f"{voltage_step:E} V")
            fmt.write_meta("current_compliance", f"{current_compliance:E} A")
            fmt.flush()

            # Write header
            fmt.write_header()
            fmt.flush()

            voltage = smu_proxy.source_voltage_level

            # SMU reading format: CURR
            smu.resource.write(":FORM:ELEM CURR")
            smu.resource.query("*OPC?")

            t0 = time.time()

            ramp = comet.Range(voltage, voltage_stop, voltage_step)
            est = Estimate(ramp.count)
            self.process.emit("progress", *est.progress)

            logging.info("ramp to end voltage: from %E V to %E V with step %E V", voltage, ramp.end, ramp.step)
            for voltage in ramp:
                logging.info("set voltage: %E V", voltage)
                smu_proxy.source_voltage_level = voltage
                time.sleep(.100)
                # smu_proxy.assert_success()
                td = time.time() - t0
                reading_current = float(smu.resource.query(":READ?").split(',')[0])
                logging.info("SMU reading: %E A", reading_current)
                self.process.emit("reading", "series", abs(voltage) if ramp.step < 0 else voltage, reading_current)

                # Environment
                if self.process.get("use_environ"):
                    with self.resources.get("environ") as environ:
                        pc_data = environ.query("GET:PC_DATA ?").split(",")
                    temperature_box = float(pc_data[2])
                    logging.info("temperature box: %s degC", temperature_box)
                    temperature_chuck = float(pc_data[33])
                    logging.info("temperature chuck: %s degC", temperature_chuck)
                    humidity_box = float(pc_data[1])
                    logging.info("humidity box: %s degC", humidity_box)
                else:
                    temperature_box = float('nan')
                    temperature_chuck = float('nan')
                    humidity_box = float('nan')

                # Write reading
                fmt.write_row(dict(
                    timestamp=td,
                    voltage=voltage,
                    current=reading_current,
                    temperature_box=temperature_box,
                    temperature_chuck=temperature_chuck,
                    humidity_box=humidity_box
                ))
                fmt.flush()
                time.sleep(waiting_time)

                est.next()
                elapsed = datetime.timedelta(seconds=round(est.elapsed.total_seconds()))
                remaining = datetime.timedelta(seconds=round(est.remaining.total_seconds()))
                self.process.emit("message", f"Elapsed {elapsed} | Remaining {remaining} | {voltage:.3f} V")
                self.process.emit("progress", *est.progress)

                # Compliance?
                compliance_tripped = smu.sense.current.protection.tripped
                if compliance_tripped:
                    logging.error("SMU in compliance")
                    raise ValueError("compliance tripped")
                # smu_proxy.assert_success()
                if not self.process.running:
                    break

        self.process.emit("progress", 0, 0)

    def finalize(self, smu):
        parameters = self.measurement_item.parameters
        voltage_step = parameters.get("voltage_step").to("V").m

        smu_proxy = create_proxy(smu)

        voltage = smu_proxy.source_voltage_level

        logging.info("ramp to zero: from %E V to %E V with step %E V", voltage, 0, voltage_step)
        for voltage in comet.Range(voltage, 0, voltage_step):
            logging.info("set voltage: %E V", voltage)
            self.process.emit("message", f"{voltage:.3f} V")
            smu_proxy.source_voltage_level = voltage
            time.sleep(.100)
            # smu_proxy.assert_success()

        smu_proxy.output_enable = False
        smu_proxy.assert_success()

        self.process.emit("progress", 5, 5)

    def code(self, *args, **kwargs):
        with self.resources.get("smu1") as smu1_res:
            smu1 = K2410(smu1_res)
            try:
                self.initialize(smu1)
                self.measure(smu1)
            finally:
                self.finalize(smu1)
