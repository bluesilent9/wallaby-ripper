"""Apply child-only limits before exec, without preexec_fn in threaded Python."""
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_FSIZE, (1024 ** 3, 1024 ** 3))
resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
os.execvp(sys.argv[1], sys.argv[1:])
