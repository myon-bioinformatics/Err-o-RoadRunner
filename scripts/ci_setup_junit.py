"""Write a separate JUnit report from GitHub Actions setup-step outcomes."""

import os
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def main() -> None:
    phase, destination = sys.argv[1:]
    suite_name = f"ci.setup.{phase}"
    suite = ET.Element("testsuite", name=suite_name)
    errors = skipped = 0
    names: set[str] = set()
    for line in os.environ["SETUP_OUTCOMES"].splitlines():
        step, outcome = line.split("=", 1)
        step = step.strip()
        if not step or step in names:
            raise ValueError("Empty or duplicate setup step name")
        names.add(step)
        case = ET.SubElement(suite, "testcase", classname=suite_name, name=step)
        if outcome == "failure":
            errors += 1
            ET.SubElement(case, "error", type="SetupError", message=f"{step} failed")
        elif outcome in ("", "skipped", "cancelled"):
            skipped += 1
            ET.SubElement(case, "skipped", message=outcome or "not run")
        elif outcome != "success":
            raise ValueError(f"Unexpected setup outcome: {outcome!r}")
    if not len(suite):
        raise ValueError("No setup outcomes to report")
    suite.attrib.update(tests=str(len(suite)), errors=str(errors), failures="0", skipped=str(skipped))
    report = Path(destination)
    report.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(report, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
