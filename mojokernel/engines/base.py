from dataclasses import dataclass, field

@dataclass
class ExecutionResult:
    stdout: str = ''
    stderr: str = ''
    success: bool = True
    ename: str = ''
    evalue: str = ''
    traceback: list[str] = field(default_factory=list)
