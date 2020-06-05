import datetime
import logging
import time
import os
import re

# TODO

# source: current
# smu: 2657

import comet
from comet.driver.keithley import K2410
from comet.driver.keithley import K6517B

from ..estimate import Estimate
from ..formatter import PQCFormatter
from .matrix import MatrixMeasurement

__all__ = ["IVRamp4WireMeasurement"]

def check_error(smu):
    error = smu.system.error
    if error[0]:
        logging.error(error)
        raise RuntimeError(f"{error[0]}: {error[1]}")

class IVRamp4WireMeasurement(MatrixMeasurement):
    """IV ramp 4wire with electrometer measurement.

    * set compliance
    * if output enabled brings source voltage to zero
    * ramps to start current
    * ramps to end current
    * ramps back to zero

    In case of compliance, stop requests or errors ramps to zero before exit.
    """

    type = "4wire_iv_ramp"

    def env_detect_model(self, env):
        try:
            env_idn = env.query("*IDN?")
        except Exception as e:
            raise RuntimeError("Failed to access Environment Box", env.resource_name, e)
        logging.info("Detected Environment Box: %s", env_idn)
        # TODO
        self.process.emit("state", dict(
            env_model=env_idn
        ))

    def initialize(self, smu, elm):
        self.process.emit("progress", 0, 5)

        parameters = self.measurement_item.parameters
        voltage_compliance = parameters.get("voltage_compliance").to("V").m
        voltage_start = parameters.get("current_start").to("A").m
        voltage_step = parameters.get("current_step").to("A").m
        waiting_time = parameters.get("waiting_time").to("s").m
        sense_mode = parameters.get("sense_mode")
        route_termination = parameters.get("route_termination", "front")
        smu_filter_enable = bool(parameters.get("smu_filter_enable", False))
        smu_filter_count = int(parameters.get("smu_filter_count", 10))
        smu_filter_type = parameters.get("smu_filter_type", "repeat")
        elm_filter_enable = bool(parameters.get("elm_filter_enable", False))
        elm_filter_count = int(parameters.get("elm_filter_count", 10))
        elm_filter_type = parameters.get("elm_filter_type", "repeat")
        zero_correction = bool(parameters.get("zero_correction", False))
        integration_rate = parameters.get("integration_rate")

        smu_idn = smu.identification
        logging.info("Detected SMU: %s", smu_idn)
        result = re.search(r'model\s+([\d\w]+)', smu_idn, re.IGNORECASE).groups()
        smu_model = ''.join(result) or None

        self.process.emit("progress", 1, 5)

        elm_idn = elm.identification
        logging.info("Detected Electrometer: %s", elm_idn)
        result = re.search(r'model\s+([\d\w]+)', elm_idn, re.IGNORECASE).groups()
        elm_model = ''.join(result) or None

        if self.process.get("use_environ"):
            with self.resources.get("environ") as environ:
                self.env_detect_model(environ)

        self.process.emit("progress", 2, 5)

        self.process.emit("state", dict(
            smu_model=smu_model,
            smu_voltage=smu.source.voltage.level,
            smu_current=smu.source.current.level,
            smu_output=smu.output,
            elm_model=elm_model,
            elm_current=None,
        ))

        # If output enabled
        if smu.output:
            voltage = smu.source.voltage.level

            logging.info("ramp to zero: from %E V to %E V with step %E V", voltage, 0, 10.0)
            for voltage in comet.Range(voltage, 0, 10.0):
                logging.info("set voltage: %E V", voltage)
                self.process.emit("message", f"{voltage:.3f} V")
                smu.source.voltage.level = voltage
                # check_error(smu)
                time.sleep(.100)
                self.process.emit("state", dict(
                    smu_voltage=voltage
                ))
                if not self.process.running:
                    break

        # Beeper off
        smu.reset()
        smu.clear()
        smu.system.beeper.status = False
        check_error(smu)

        self.process.emit("state", dict(
            smu_voltage=smu.source.voltage.level,
            smu_current=smu.source.current.level,
            smu_output=smu.output,
            elm_current=None
        ))

        # Select rear terminal
        if route_termination == "front":
            smu.resource.write(":ROUT:TERM FRONT")
        elif route_termination == "rear":
            smu.resource.write(":ROUT:TERM REAR")
        smu.resource.query("*OPC?")
        check_error(smu)

        # set sense mode
        logging.info("set sense mode: '%s'", sense_mode)
        if sense_mode == "remote":
            smu.resource.write(":SYST:RSEN ON")
        elif sense_mode == "local":
            smu.resource.write(":SYST:RSEN OFF")
        else:
            raise ValueError(f"invalid sense mode: {sense_mode}")
        smu.resource.query("*OPC?")
        check_error(smu)

        # Compliance
        logging.info("set compliance: %E A", voltage_compliance)
        smu.sense.current.protection.level = voltage_compliance
        check_error(smu)

        # Range
        current_range = 1.05E-6
        smu.resource.write(":SENS:CURR:RANG:AUTO ON")
        smu.resource.write(":SENS:VOLT:RANG:AUTO ON")
        smu.resource.query("*OPC?")
        check_error(smu)
        #smu.resource.write(f":SENS:CURR:RANG {current_range:E}")
        #smu.resource.query("*OPC?")
        #check_error(smu)

        # Filter
        smu.resource.write(f":SENS:AVER:COUN {smu_filter_count:d}")
        smu.resource.query("*OPC?")
        check_error(smu)

        if smu_filter_type == "repeat":
            smu.resource.write(":SENS:AVER:TCON REP")
        elif smu_filter_type == "repeat":
            smu.resource.write(":SENS:AVER:TCON MOV")
        smu.resource.query("*OPC?")
        check_error(smu)

        if smu_filter_enable:
            smu.resource.write(":SENS:AVER:STATE ON")
        else:
            smu.resource.write(":SENS:AVER:STATE OFF")
        smu.resource.query("*OPC?")
        check_error(smu)

        self.process.emit("progress", 1, 5)

        # If output disabled
        voltage = 0
        smu.source.voltage.level = voltage
        smu.resource.write(":SOUR:FUNC 'CURR'")
        smu.resource.query("*OPC?")
        check_error(smu)
        smu.output = True
        check_error(smu)
        time.sleep(.100)

        self.process.emit("state", dict(
            smu_output=smu.output
        ))

        self.process.emit("progress", 2, 5)

        if self.process.running:

            current = smu.source.current.level

            logging.info("ramp to start current: from %E A to %E A with step %E A", current, current_start, current_step)
            for current in comet.Range(current, current_start, current_step):
                logging.info("set current: %E A", current)
                self.process.emit("message", f"{current:.3f} A")
                smu.source.current.level = current
                # check_error(smu)
                time.sleep(.100)
                time.sleep(waiting_time)

                self.process.emit("state", dict(
                    smu_voltage=voltage,
                ))
                # Compliance?
                compliance_tripped = smu.sense.current.protection.tripped
                if compliance_tripped:
                    logging.error("SMU in compliance")
                    raise ValueError("compliance tripped")
                if not self.process.running:
                    break

        def elm_safe_write(message):
            """Write, wait for operation complete, test for errors."""
            elm.resource.write(message)
            elm.resource.query("*OPC?")
            code, label = elm.resource.query(":SYST:ERR?").split(",", 1)
            code = int(code)
            label = label.strip("\"")
            if code != 0:
                logging.error(f"error {code}: {label} returned by '{message}'")
                raise RuntimeError(f"error {code}: {label} returned by '{message}'")

        elm_safe_write("*RST")
        elm_safe_write("*CLS")

        # Filter
        elm_safe_write(f":SENS:CURR:AVER:COUN {elm_filter_count:d}")

        if elm_filter_type == "repeat":
            elm_safe_write(":SENS:CURR:AVER:TCON REP")
        elif elm_filter_type == "repeat":
            elm_safe_write(":SENS:CURR:AVER:TCON MOV")

        if elm_filter_enable:
            elm_safe_write(":SENS:CURR:AVER:STATE ON")
        else:
            elm_safe_write(":SENS:CURR:AVER:STATE OFF")

        nplc = integration_rate / 10.
        elm_safe_write(f":SENS:CURR:NPLC {nplc:02f}")

        elm_safe_write(":SYST:ZCH ON") # enable zero check
        assert elm.resource.query(":SYST:ZCH?") == '1', "failed to enable zero check"

        elm_safe_write(":SENS:FUNC 'CURR'") # note the quotes!
        assert elm.resource.query(":SENS:FUNC?") == '"CURR:DC"', "failed to set sense function to current"

        elm_safe_write(":SENS:CURR:RANG 20e-12") # 20pA
        if zero_correction:
            elm_safe_write(":SYST:ZCOR ON") # perform zero correction
        elm_safe_write(":SENS:CURR:RANG:AUTO ON")
        elm_safe_write(":SENS:CURR:RANG:AUTO:LLIM 2.000000E-11")
        elm_safe_write(":SENS:CURR:RANG:AUTO:ULIM 2.000000E-2")

        elm_safe_write(":SYST:ZCH OFF") # disable zero check
        assert elm.resource.query(":SYST:ZCH?") == '0', "failed to disable zero check"

        self.process.emit("progress", 3, 5)

    def measure(self, smu, elm):
        sample_name = self.sample_name
        sample_type = self.sample_type
        output_dir = self.output_dir
        contact_name = self.measurement_item.contact.name
        measurement_name = self.measurement_item.name
        parameters = self.measurement_item.parameters
        voltage_compliance = parameters.get("voltage_compliance").to("A").m
        current_start = parameters.get("current_start").to("A").m
        current_step = parameters.get("current_step").to("A").m
        current_stop = parameters.get("current_stop").to("A").m
        waiting_time = parameters.get("waiting_time").to("s").m

        if self.process.running:
            iso_timestamp = comet.make_iso()
            filename = comet.safe_filename(f"{iso_timestamp}-{sample_name}-{sample_type}-{contact_name}-{measurement_name}.txt")
            with open(os.path.join(output_dir, filename), "w", newline="") as f:
                # Create formatter
                fmt = PQCFormatter(f)
                fmt.add_column("timestamp", ".3f")
                fmt.add_column("current", "E")
                fmt.add_column("voltage_smu", "E")
                fmt.add_column("voltage_elm", "E")
                fmt.add_column("temperature_box", "E")
                fmt.add_column("temperature_chuck", "E")
                fmt.add_column("humidity_box", "E")

                # Write meta data
                fmt.write_meta("sample_name", sample_name)
                fmt.write_meta("sample_type", sample_type)
                fmt.write_meta("contact_name", contact_name)
                fmt.write_meta("measurement_name", measurement_name)
                fmt.write_meta("measurement_type", self.type)
                fmt.write_meta("start_timestamp", datetime.datetime.now(), "%Y-%m-%d %H:%M:%S")
                fmt.write_meta("current_start", f"{current_start:E} V")
                fmt.write_meta("current_stop", f"{current_stop:E} V")
                fmt.write_meta("current_step", f"{current_step:E} V")
                fmt.write_meta("voltage_compliance", f"{voltage_compliance:E} A")
                fmt.flush()

                # Write header
                fmt.write_header()
                fmt.flush()

                current = smu.source.current.level

                # SMU reading format: VOLT
                smu.resource.write(":FORM:ELEM VOLT")
                smu.resource.query("*OPC?")

                # Electrometer reading format: READ
                elm.resource.write(":FORM:ELEM READ")
                elm.resource.query("*OPC?")

                ramp = comet.Range(current, current_stop, current_step)
                est = Estimate(ramp.count)
                self.process.emit("progress", *est.progress)

                t0 = time.time()

                logging.info("ramp to end current: from %E A to %E A with step %E A", current, ramp.end, ramp.step)
                for current in ramp:
                    logging.info("set current: %E A", current)
                    smu.clear()
                    smu.source.current.level = voltage
                    time.sleep(.100)
                    # check_error(smu)
                    dt = time.time() - t0

                    est.next()
                    elapsed = datetime.timedelta(seconds=round(est.elapsed.total_seconds()))
                    remaining = datetime.timedelta(seconds=round(est.remaining.total_seconds()))
                    self.process.emit("message", f"Elapsed {elapsed} | Remaining {remaining} | {voltage:.3f} V")
                    self.process.emit("progress", *est.progress)

                    # read SMU
                    smu_reading = float(smu.resource.query(":READ?").split(',')[0])
                    logging.info("SMU reading: %E", smu_reading)
                    self.process.emit("reading", "smu", smu_reading, current)

                    # read ELM
                    elm_reading = float(elm.resource.query(":READ?").split(',')[0])
                    logging.info("ELM reading: %E", elm_reading)
                    self.process.emit("reading", "elm", elm_reading, current)

                    self.process.emit("update", )
                    self.process.emit("state", dict(
                        smu_current=current,
                        smu_voltage=smu_reading,
                        elm_voltage=elm_reading
                    ))

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

                    self.process.emit("state", dict(
                        env_chuck_temperature=temperature_chuck,
                        env_box_temperature=temperature_box,
                        env_box_humidity=humidity_box
                    ))

                    # Write reading
                    fmt.write_row(dict(
                        timestamp=dt,
                        current=current,
                        voltage_smu=smu_reading,
                        voltage_elm=elm_reading,
                        temperature_box=temperature_box,
                        temperature_chuck=temperature_chuck,
                        humidity_box=humidity_box
                    ))
                    fmt.flush()
                    time.sleep(waiting_time)

                    # Compliance?
                    compliance_tripped = smu.sense.current.protection.tripped
                    if compliance_tripped:
                        logging.error("SMU in compliance")
                        raise ValueError("compliance tripped")
                    # check_error(smu)
                    if not self.process.running:
                        break

        self.process.emit("progress", 4, 5)

    def finalize(self, smu, elm):
        elm.resource.write(":SYST:ZCH ON")
        elm.resource.query("*OPC?")

        self.process.emit("state", dict(
            smu_current=None,
            elm_current=None
        ))

        parameters = self.measurement_item.parameters
        current_step = parameters.get("current_step").to("A").m
        current = smu.source.current.level

        logging.info("ramp to zero: from %E A to %E A with step %E A", current, 0, current_step)
        for current in comet.Range(current, 0, current_step):
            logging.info("set current: %E A", current)
            self.process.emit("message", f"{current:.3f} A")
            smu.source.current.level = current
            time.sleep(.100)
            # check_error(smu)
            self.process.emit("state", dict(
                smu_current=current,
            ))

        smu.output = False
        check_error(smu)

        self.process.emit("state", dict(
            smu_output=smu.output,
            env_chuck_temperature=None,
            env_box_temperature=None,
            env_box_humidity=None
        ))

        self.process.emit("progress", 5, 5)

    def code(self, *args, **kwargs):
        with self.resources.get("smu1") as smu1_res:
            smu1 = K2410(smu1_res)
            with self.resources.get("elm") as elm_res:
                elm = K6517B(elm_res)
                try:
                    self.initialize(smu1, elm)
                    self.measure(smu1, elm)
                finally:
                    self.finalize(smu1, elm)
