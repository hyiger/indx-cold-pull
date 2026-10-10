"""generate_gcode() and build_steps() must match the page's generateGcode() and buildSteps().

The page is the reference (it is itself kept byte-identical to the PrusaSlicer fork). Its <script> is
evaluated in node, and the two functions are compared over every nozzle and a
spread of temperatures. INDX_COLD_PULL_PAGE overrides the page's path (default: the repo's index.html).
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import cold_pull_core as core  # noqa: E402

PAGE = os.environ.get("INDX_COLD_PULL_PAGE", os.path.join(os.path.dirname(__file__), "..", "..", "index.html"))

NODE_SCRIPT = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[1], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const grab = (name) => script.match(new RegExp("function " + name + "\\([\\s\\S]*?\\n}\\n"))[0];
eval(grab("generateGcode") + grab("buildSteps") +
     ";globalThis.generateGcode = generateGcode; globalThis.buildSteps = buildSteps;");
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([tool, flush, pull, fil]) => ({
  gcode: generateGcode(tool, flush, pull),
  steps: buildSteps(tool, flush, pull, fil),
}));
process.stdout.write(JSON.stringify(out));
"""

CASES = [(tool, flush, pull, fil)
         for tool in range(8)
         for flush, pull in ((290, 80), (200, 60), (245, 120), (255, 95))
         for fil in ("PETG", None)]


@unittest.skipUnless(shutil.which("node") and os.path.exists(PAGE), "needs node and ../index.html")
class ParityWithPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["node", "-e", NODE_SCRIPT, PAGE, json.dumps(CASES)],
                                capture_output=True, text=True, check=True)
        cls.reference = json.loads(result.stdout)

    def test_gcode_is_byte_identical(self):
        for (tool, flush, pull, _), ref in zip(CASES, self.reference):
            with self.subTest(tool=tool, flush=flush, pull=pull):
                self.assertEqual(core.generate_gcode(tool, flush, pull), ref["gcode"])

    def test_steps_match(self):
        for (tool, flush, pull, fil), ref in zip(CASES, self.reference):
            with self.subTest(tool=tool, flush=flush, pull=pull, fil=fil):
                ours = [[caption, cmd, timeout * 1000] for caption, cmd, timeout in
                        core.build_steps(tool, flush, pull, fil)]
                self.assertEqual(ours, ref["steps"])


if __name__ == "__main__":
    unittest.main()
