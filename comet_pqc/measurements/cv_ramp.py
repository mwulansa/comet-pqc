import random
import time
import os

from ..utils import auto_step, safe_filename
from .matrix import MatrixMeasurement

__all__ = ["CVRampMeasurement"]

class CVRampMeasurement(MatrixMeasurement):
    """CV ramp measurement."""

    type = "cv_ramp"

    def measure(self):
        sample_name = self.sample_name
        sample_type = self.sample_type
        output_dir = self.output_dir
        contact_name =  self.measurement_item.contact.name
        measurement_name =  self.measurement_item.name

        filename = safe_filename(f"{sample_name}-{sample_type}-{contact_name}-{measurement_name}.txt")
        with open(os.path.join(output_dir, filename), "w") as f:
            # TODO
            f.write(f"sample_name: {sample_name}\n")
            f.write(f"sample_type: {sample_type}\n")
            f.write(f"contact_name: {contact_name}\n")
            f.write(f"measurement_name: {measurement_name}\n")
            f.write(f"measurement_type: {self.type}\n")
            f.flush()

    def code(self, *args, **kwargs):
        self.measure()
        time.sleep(random.uniform(2.5, 4.0))
