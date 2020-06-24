import datetime
import logging
import time
import os

import comet

from ..driver import K2410
from ..driver import K2657A
from ..utils import format_metric
from ..estimate import Estimate
from ..formatter import PQCFormatter
from .matrix import MatrixMeasurement

__all__ = ["IVRampBiasMeasurement"]

def check_error(hvsrc):
    if hvsrc.errorqueue.count:
        error = hvsrc.errorqueue.next()
        logging.error(error)
        raise RuntimeError(f"{error[0]}: {error[1]}")

class IVRampBiasMeasurement(MatrixMeasurement):
    """Bias IV ramp measurement."""

    type = "iv_ramp_bias"

    def __init__(self, process):
        super().__init__(process)
        self.register_parameter('voltage_start', unit='V', required=True)
        self.register_parameter('voltage_stop', unit='V', required=True)
        self.register_parameter('voltage_step', unit='V', required=True)
        self.register_parameter('waiting_time', unit='s', required=True)
        self.register_parameter('bias_voltage', unit='V', required=True)
        self.register_parameter('bias_mode', 'constant', values=('constant', 'offset'))
        self.register_parameter('vsrc_current_compliance', unit='A', required=True)
        self.register_parameter('vsrc_sense_mode', 'local', values=('local', 'remote'))
        self.register_parameter('vsrc_route_termination', 'rear', values=('front', 'rear'))
        self.register_parameter('vsrc_filter_enable', False, type=bool)
        self.register_parameter('vsrc_filter_count', 10, type=int)
        self.register_parameter('vsrc_filter_type', 'repeat', values=('repeat', 'moving'))
        self.register_parameter('hvsrc_current_compliance', unit='A', required=True)
        self.register_parameter('hvsrc_sense_mode', 'local', values=('local', 'remote'))
        self.register_parameter('hvsrc_filter_enable', False, type=bool)
        self.register_parameter('hvsrc_filter_count', 10, type=int)
        self.register_parameter('hvsrc_filter_type','repeat', values=('repeat', 'moving'))

    def initialize(self, vsrc, hvsrc):
        self.process.emit("progress", 1, 5)
        self.process.emit("message", "Ramp to start...")

        voltage_start = self.get_parameter('voltage_start')
        voltage_stop = self.get_parameter('voltage_stop')
        voltage_step = self.get_parameter('voltage_step')
        waiting_time = self.get_parameter('waiting_time')
        bias_voltage = self.get_parameter('bias_voltage')
        bias_mode = self.get_parameter('bias_mode')
        vsrc_current_compliance = self.get_parameter('vsrc_current_compliance')
        vsrc_sense_mode = self.get_parameter('vsrc_sense_mode')
        vsrc_route_termination = self.get_parameter('vsrc_route_termination')
        vsrc_filter_enable = self.get_parameter('vsrc_filter_enable')
        vsrc_filter_count = self.get_parameter('vsrc_filter_count')
        vsrc_filter_type = self.get_parameter('vsrc_filter_type')
        hvsrc_current_compliance = self.get_parameter('hvsrc_current_compliance')
        hvsrc_sense_mode = self.get_parameter('hvsrc_sense_mode')
        hvsrc_filter_enable = self.get_parameter('hvsrc_filter_enable')
        hvsrc_filter_count = self.get_parameter('hvsrc_filter_count')
        hvsrc_filter_type = self.get_parameter('hvsrc_filter_type')

        # Initialize V Source

        vsrc.reset()
        vsrc.clear()
        vsrc.system.beeper.status = False

        self.process.emit("state", dict(
            vsrc_voltage=vsrc.source.voltage.level,
            vsrc_current=None,
            vsrc_output=vsrc.output
        ))

        # Select rear terminal
        logging.info("set route termination: '%s'", vsrc_route_termination)
        if vsrc_route_termination == "front":
            vsrc.resource.write(":ROUT:TERM FRONT")
        elif vsrc_route_termination == "rear":
            vsrc.resource.write(":ROUT:TERM REAR")
        vsrc.resource.query("*OPC?")

        # set sense mode
        logging.info("set sense mode: '%s'", vsrc_sense_mode)
        if vsrc_sense_mode == "remote":
            vsrc.resource.write(":SYST:RSEN ON")
        elif vsrc_sense_mode == "local":
            vsrc.resource.write(":SYST:RSEN OFF")
        else:
            raise ValueError(f"invalid sense mode: {vsrc_sense_mode}")
        vsrc.resource.query("*OPC?")

        # Compliance
        logging.info("set compliance: %E A", vsrc_current_compliance)
        vsrc.sense.current.protection.level = vsrc_current_compliance

        # Range
        current_range = 1.05E-6
        vsrc.resource.write(":SENS:CURR:RANG:AUTO ON")
        vsrc.resource.query("*OPC?")
        vsrc.resource.write(":SENS:VOLT:RANG:AUTO ON")
        vsrc.resource.query("*OPC?")

        # Filter
        vsrc.resource.write(f":SENS:AVER:COUN {vsrc_filter_count:d}")
        vsrc.resource.query("*OPC?")

        if vsrc_filter_type == "repeat":
            vsrc.resource.write(":SENS:AVER:TCON REP")
        elif vsrc_filter_type == "repeat":
            vsrc.resource.write(":SENS:AVER:TCON MOV")
        vsrc.resource.query("*OPC?")

        if vsrc_filter_enable:
            vsrc.resource.write(":SENS:AVER:STATE ON")
        else:
            vsrc.resource.write(":SENS:AVER:STATE OFF")
        vsrc.resource.query("*OPC?")

        if not self.process.running:
            return

        # Initialize HV Source

        hvsrc.reset()
        hvsrc.clear()
        hvsrc.beeper.enable = False

        self.process.emit("state", dict(
            hvsrc_voltage=hvsrc.source.levelv,
            hvsrc_current=None,
            hvsrc_output=hvsrc.source.output
        ))

        # set sense mode
        logging.info("set sense mode: '%s'", hvsrc_sense_mode)
        if hvsrc_sense_mode == "remote":
            hvsrc.sense = 'REMOTE'
        elif hvsrc_sense_mode == "local":
            hvsrc.sense = 'LOCAL'
        else:
            raise ValueError(f"invalid sense mode: {hvsrc_sense_mode}")
        check_error(hvsrc)

        # Current source
        hvsrc.source.func = 'DCVOLTS'
        check_error(hvsrc)

        # Compliance
        logging.info("set compliance: %E A", hvsrc_current_compliance)
        hvsrc.source.limiti = hvsrc_current_compliance
        check_error(hvsrc)
        logging.info("compliance: %E A", hvsrc.source.limiti)

        # Filter
        hvsrc.measure.filter.count = hvsrc_filter_count
        check_error(hvsrc)

        if hvsrc_filter_type == "repeat":
            hvsrc.measure.filter.type = 'REPEAT'
        elif hvsrc_filter_type == "moving":
            hvsrc.measure.filter.type = 'MOVING'
        check_error(hvsrc)

        hvsrc.measure.filter.enable = hvsrc_filter_enable
        check_error(hvsrc)

        if not self.process.running:
            return

        # Output enable

        vsrc.output = True
        time.sleep(.100)
        self.process.emit("state", dict(
            vsrc_output=vsrc.output
        ))
        hvsrc.source.output = 'ON'
        time.sleep(.100)
        self.process.emit("state", dict(
            hvsrc_output=hvsrc.source.output
        ))

        # Ramp HV Spource to bias voltage
        voltage = hvsrc.source.levelv

        logging.info("ramp to bias voltage: from %E V to %E V with step %E V", voltage, bias_voltage, 1.0)
        for voltage in comet.Range(voltage, bias_voltage, 1.0):
            logging.info("set bias voltage: %E V", voltage)
            self.process.emit("message", "Ramp to bias... {}".format(format_metric(voltage, "V")))
            hvsrc.source.levelv = voltage
            self.process.emit("state", dict(
                hvsrc_voltage=voltage,
            ))
            time.sleep(waiting_time)

            # Compliance?
            compliance_tripped = hvsrc.source.compliance
            if compliance_tripped:
                logging.error("HV Source in compliance")
                raise ValueError("HV Source compliance tripped")

            if not self.process.running:
                break

        # Ramp V Source to start voltage
        voltage = vsrc.source.voltage.level

        logging.info("ramp to start voltage: from %E V to %E V with step %E V", voltage, voltage_start, 1.0)
        for voltage in comet.Range(voltage, voltage_start, 1.0):
            logging.info("set voltage: %E V", voltage)
            self.process.emit("message", "Ramp to start... {}".format(format_metric(voltage, "V")))
            vsrc.source.voltage.level = voltage
            self.process.emit("state", dict(
                vsrc_voltage=voltage,
            ))
            # check_error(vsrc)
            time.sleep(waiting_time)

            # Compliance?
            compliance_tripped = vsrc.sense.current.protection.tripped
            if compliance_tripped:
                logging.error("V Source in compliance")
                raise ValueError("V Source compliance tripped")

            if not self.process.running:
                break

        self.process.emit("progress", 5, 5)

    def measure(self, vsrc, hvsrc):
        self.process.emit("progress", 1, 2)
        self.process.emit("message", "Ramp to...")

        sample_name = self.sample_name
        sample_type = self.sample_type
        output_dir = self.output_dir
        contact_name = self.measurement_item.contact.name
        measurement_name = self.measurement_item.name

        voltage_start = self.get_parameter('voltage_start')
        voltage_stop = self.get_parameter('voltage_stop')
        voltage_step = self.get_parameter('voltage_step')
        waiting_time = self.get_parameter('waiting_time')
        bias_voltage = self.get_parameter('bias_voltage')
        bias_mode = self.get_parameter('bias_mode')
        vsrc_current_compliance = self.get_parameter('vsrc_current_compliance')
        vsrc_sense_mode = self.get_parameter('vsrc_sense_mode')
        vsrc_route_termination = self.get_parameter('vsrc_route_termination')
        vsrc_filter_enable = bool(self.get_parameter('vsrc_filter_enable'))
        vsrc_filter_count = int(self.get_parameter('vsrc_filter_count'))
        vsrc_filter_type = self.get_parameter('vsrc_filter_type')
        hvsrc_current_compliance = self.get_parameter('hvsrc_current_compliance')
        hvsrc_sense_mode = self.get_parameter('hvsrc_sense_mode')
        hvsrc_filter_enable = bool(self.get_parameter('hvsrc_filter_enable'))
        hvsrc_filter_count = int(self.get_parameter('hvsrc_filter_count'))
        hvsrc_filter_type = self.get_parameter('hvsrc_filter_type')

        if not self.process.running:
            return

        iso_timestamp = comet.make_iso()
        filename = comet.safe_filename(f"{iso_timestamp}-{sample_name}-{sample_type}-{contact_name}-{measurement_name}.txt")
        with open(os.path.join(output_dir, self.create_filename()), "w", newline="") as f:
            # Create formatter
            fmt = PQCFormatter(f)
            fmt.add_column("timestamp", ".3f")
            fmt.add_column("voltage", "E")
            fmt.add_column("current", "E")
            fmt.add_column("bias_voltage", "E")
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
            fmt.write_meta("voltage_start", f"{voltage_start:G} V")
            fmt.write_meta("voltage_stop", f"{voltage_stop:G} V")
            fmt.write_meta("voltage_step", f"{voltage_step:G} V")
            fmt.write_meta("waiting_time", f"{waiting_time:G} s")
            fmt.write_meta("bias_voltage", f"{bias_voltage:G} V")
            fmt.write_meta("vsrc_current_compliance", f"{vsrc_current_compliance:G} A")
            fmt.write_meta("vsrc_sense_mode", vsrc_sense_mode)
            fmt.write_meta("vsrc_route_termination", vsrc_route_termination)
            fmt.write_meta("vsrc_filter_enable", format(vsrc_filter_enable).lower())
            fmt.write_meta("vsrc_filter_count", format(vsrc_filter_count))
            fmt.write_meta("vsrc_filter_type", vsrc_filter_type)
            fmt.write_meta("hvsrc_current_compliance", f"{hvsrc_current_compliance:G} A")
            fmt.write_meta("hvsrc_sense_mode", hvsrc_sense_mode)
            fmt.write_meta("hvsrc_filter_enable", format(hvsrc_filter_enable).lower())
            fmt.write_meta("hvsrc_filter_count", format(hvsrc_filter_count))
            fmt.write_meta("hvsrc_filter_type", hvsrc_filter_type)
            fmt.flush()

            # Write header
            fmt.write_header()
            fmt.flush()

            # V Source reading format: CURR
            vsrc.resource.write(":FORM:ELEM CURR")
            vsrc.resource.query("*OPC?")

            voltage = vsrc.source.voltage.level

            ramp = comet.Range(voltage, voltage_stop, voltage_step)
            est = Estimate(ramp.count)
            self.process.emit("progress", *est.progress)

            t0 = time.time()

            logging.info("ramp to end voltage: from %E V to %E V with step %E V", voltage, ramp.end, ramp.step)
            for voltage in ramp:
                logging.info("set voltage: %E V", voltage)
                vsrc.source.voltage.level = voltage
                self.process.emit("state", dict(
                    vsrc_voltage=voltage,
                ))
                # Move bias TODO
                if bias_mode == "offset":
                    bias_voltage += abs(ramp.step) if ramp.begin <= ramp.end else -abs(ramp.step)
                    logging.info("set bias voltage: %E V", bias_voltage)
                    hvsrc.source.levelv = bias_voltage
                    self.process.emit("state", dict(
                        hvsrc_voltage=bias_voltage,
                    ))
                time.sleep(.100)
                dt = time.time() - t0

                est.next()
                elapsed = datetime.timedelta(seconds=round(est.elapsed.total_seconds()))
                remaining = datetime.timedelta(seconds=round(est.remaining.total_seconds()))
                self.process.emit("message", "Elapsed {} | Remaining {} | {} | Bias {}".format(elapsed, remaining, format_metric(voltage, "V"), format_metric(bias_voltage, "V")))
                self.process.emit("progress", *est.progress)

                # read HV Source
                hvsrc_reading = hvsrc.measure.i()
                logging.info("HV Source reading: %E A", hvsrc_reading)
                self.process.emit("reading", "hvsrc", abs(voltage) if ramp.step < 0 else voltage, hvsrc_reading)

                # read V Source
                vsrc_reading = float(vsrc.resource.query(":READ?").split(',')[0])
                logging.info("V Source bias reading: %E A", vsrc_reading)

                self.process.emit("update")
                self.process.emit("state", dict(
                    vsrc_current=vsrc_reading,
                    hvsrc_current=hvsrc_reading
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
                    voltage=voltage,
                    current=hvsrc_reading,
                    bias_voltage=bias_voltage,
                    temperature_box=temperature_box,
                    temperature_chuck=temperature_chuck,
                    humidity_box=humidity_box
                ))
                fmt.flush()
                time.sleep(waiting_time)

                # Compliance?
                compliance_tripped = vsrc.sense.current.protection.tripped
                if compliance_tripped:
                    logging.error("V Source in compliance")
                    raise ValueError("V Source compliance tripped")
                compliance_tripped = hvsrc.source.compliance
                if compliance_tripped:
                    logging.error("HV Source in compliance")
                    raise ValueError("HV Source compliance tripped")
                if not self.process.running:
                    break

        self.process.emit("progress", 2, 2)

    def finalize(self, vsrc, hvsrc):
        self.process.emit("progress", 1, 2)
        self.process.emit("message", "Ramp to zero...")

        voltage_step = self.get_parameter('voltage_step')

        voltage = vsrc.source.voltage.level

        logging.info("ramp to zero: from %E V to %E V with step %E V", voltage, 0, 1.0)
        for voltage in comet.Range(voltage, 0, 1.0):
            logging.info("set voltage: %E V", voltage)
            self.process.emit("message", "Ramp to zero... {}".format(format_metric(voltage, "V")))
            vsrc.source.voltage.level = voltage
            self.process.emit("state", dict(
                vsrc_voltage=voltage,
            ))
            time.sleep(.100)

        bias_voltage = hvsrc.source.levelv

        logging.info("ramp bias to zero: from %E V to %E V with step %E V", bias_voltage, 0, 1.0)
        for voltage in comet.Range(bias_voltage, 0, 1.0):
            logging.info("set bias voltage: %E V", voltage)
            self.process.emit("message", "Ramp bias to zero... {}".format(format_metric(voltage, "V")))
            hvsrc.source.levelv = voltage
            self.process.emit("state", dict(
                hvsrc_voltage=voltage,
            ))
            time.sleep(.100)

        vsrc.output = False
        hvsrc.source.output = 'OFF'

        self.process.emit("state", dict(
            vsrc_output=vsrc.output,
            vsrc_voltage=None,
            vsrc_current=None,
            hvsrc_output=hvsrc.source.output,
            hvsrc_voltage=None,
            hvsrc_current=None,
            env_chuck_temperature=None,
            env_box_temperature=None,
            env_box_humidity=None
        ))

        self.process.emit("progress", 2, 2)

    def code(self, *args, **kwargs):
        with self.resources.get("vsrc") as vsrc_res:
            with self.resources.get("hvsrc") as hvsrc_res:
                vsrc = K2410(vsrc_res)
                hvsrc = K2657A(hvsrc_res)
                try:
                    self.initialize(vsrc, hvsrc)
                    self.measure(vsrc, hvsrc)
                finally:
                    self.finalize(vsrc, hvsrc)
