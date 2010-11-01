"""
Tests that run inside GDB.

Note: debug information is already imported by the file generated by
Cython.Debugger.Cygdb.make_command_file()
"""

import os
import sys
import trace
import inspect
import warnings
import unittest
import traceback
from test import test_support

import gdb

from Cython.Debugger import libcython
from Cython.Debugger import libpython
from Cython.Debugger.Tests import TestLibCython as test_libcython


# for some reason sys.argv is missing in gdb
sys.argv = ['gdb']


class DebugTestCase(unittest.TestCase):
    """
    Base class for test cases. On teardown it kills the inferior and unsets 
    all breakpoints.
    """
    
    def __init__(self, name):
        super(DebugTestCase, self).__init__(name)
        self.cy = libcython.cy
        self.module = libcython.cy.cython_namespace['codefile']
        self.spam_func, self.spam_meth = libcython.cy.functions_by_name['spam']
        self.ham_func = libcython.cy.functions_by_qualified_name[
            'codefile.ham']
        self.eggs_func = libcython.cy.functions_by_qualified_name[
            'codefile.eggs']
    
    def read_var(self, varname, cast_to=None):
        result = gdb.parse_and_eval('$cy_cname("%s")' % varname)
        if cast_to:
            result = cast_to(result)
        
        return result
    
    def local_info(self):
        return gdb.execute('info locals', to_string=True)
    
    def lineno_equals(self, source_line=None, lineno=None):
        if source_line is not None:
            lineno = test_libcython.source_to_lineno[source_line]
        frame = gdb.selected_frame()
        self.assertEqual(libcython.cy.step.lineno(frame), lineno)

    def break_and_run(self, source_line):
        break_lineno = test_libcython.source_to_lineno[source_line]
        gdb.execute('cy break codefile:%d' % break_lineno, to_string=True)
        gdb.execute('run', to_string=True)

    def tearDown(self):
        gdb.execute('delete breakpoints', to_string=True)
        try:
            gdb.execute('kill inferior 1', to_string=True)
        except RuntimeError:
            pass
            

class TestDebugInformationClasses(DebugTestCase):
    
    def test_CythonModule(self):
        "test that debug information was parsed properly into data structures"
        self.assertEqual(self.module.name, 'codefile')
        global_vars = ('c_var', 'python_var', '__name__', 
                       '__builtins__', '__doc__', '__file__')
        assert set(global_vars).issubset(self.module.globals)
        
    def test_CythonVariable(self):
        module_globals = self.module.globals
        c_var = module_globals['c_var']
        python_var = module_globals['python_var']
        self.assertEqual(c_var.type, libcython.CObject)
        self.assertEqual(python_var.type, libcython.PythonObject)
        self.assertEqual(c_var.qualified_name, 'codefile.c_var')
    
    def test_CythonFunction(self):
        self.assertEqual(self.spam_func.qualified_name, 'codefile.spam')
        self.assertEqual(self.spam_meth.qualified_name, 
                         'codefile.SomeClass.spam')
        self.assertEqual(self.spam_func.module, self.module)
        
        assert self.eggs_func.pf_cname
        assert not self.ham_func.pf_cname
        assert not self.spam_func.pf_cname
        assert not self.spam_meth.pf_cname
        
        self.assertEqual(self.spam_func.type, libcython.CObject)
        self.assertEqual(self.ham_func.type, libcython.CObject)
        
        self.assertEqual(self.spam_func.arguments, ['a'])
        self.assertEqual(self.spam_func.step_into_functions, 
                         set(['puts', 'some_c_function']))
        
        expected_lineno = test_libcython.source_to_lineno['def spam(a=0):']
        self.assertEqual(self.spam_func.lineno, expected_lineno)
        self.assertEqual(sorted(self.spam_func.locals), list('abcd'))


class TestParameters(unittest.TestCase):
    
    def test_parameters(self):
        assert libcython.parameters.colorize_code
        gdb.execute('set cy_colorize_code off')
        assert not libcython.parameters.colorize_code


class TestBreak(DebugTestCase):

    def test_break(self):
        result = libpython._execute('cy break codefile.spam', to_string=True)
        assert self.spam_func.cname in result
        
        self.assertEqual(len(gdb.breakpoints()), 1)
        bp, = gdb.breakpoints()
        self.assertEqual(bp.type, gdb.BP_BREAKPOINT)
        self.assertEqual(bp.location, self.spam_func.cname)
        assert bp.enabled

        
class DebugStepperTestCase(DebugTestCase):
    
    def step(self, varnames_and_values, source_line=None, lineno=None):
        gdb.execute(self.command, to_string=True)
        for varname, value in varnames_and_values:
            self.assertEqual(self.read_var(varname), value, self.local_info())
        
        self.lineno_equals(source_line, lineno)


class TestStep(DebugStepperTestCase):
    """
    Test stepping. Stepping happens in the code found in 
    Cython/Debugger/Tests/codefile.
    """
    
    def test_cython_step(self):
        gdb.execute('cy break codefile.spam')
        libcython.parameters.step_into_c_code.value = False
        
        gdb.execute('run', to_string=True)
        self.lineno_equals('def spam(a=0):')
        
        gdb.execute('cy step', to_string=True)
        self.lineno_equals('b = c = d = 0')
        
        self.command = 'cy step'
        self.step([('b', 0)], source_line='b = 1')
        self.step([('b', 1), ('c', 0)], source_line='c = 2')
        self.step([('c', 2)], source_line='int(10)')
        self.step([], source_line='puts("spam")')
        self.step([], source_line='os.path.join("foo", "bar")')
        
        gdb.execute('cont', to_string=True)
        self.assertEqual(len(gdb.inferiors()), 1)
        self.assertEqual(gdb.inferiors()[0].pid, 0)
    
    def test_c_step(self):
        libcython.parameters.step_into_c_code.value = True
        self.break_and_run('some_c_function()')
        gdb.execute('cy step', to_string=True)
        self.assertEqual(gdb.selected_frame().name(), 'some_c_function')
    
    def test_python_step(self):
        self.break_and_run('os.path.join("foo", "bar")')
        
        gdb.execute('cy step', to_string=True)
        
        curframe = gdb.selected_frame()
        self.assertEqual(curframe.name(), 'PyEval_EvalFrameEx')
        
        pyframe = libpython.Frame(curframe).get_pyop()
        self.assertEqual(str(pyframe.co_name), 'join')


class TestNext(DebugStepperTestCase):
    
    def test_cython_next(self):
        libcython.parameters.step_into_c_code.value = True
        self.break_and_run('c = 2')

        lines = (
            'int(10)',
            'puts("spam")',
            'os.path.join("foo", "bar")',
            'some_c_function()',
        )

        for line in lines:
            gdb.execute('cy next')
            self.lineno_equals(line)


class TestLocalsGlobals(DebugTestCase):
    
    def test_locals(self):
        self.break_and_run('int(10)')
        
        result = gdb.execute('cy locals', to_string=True)
        assert 'a = 0' in result, repr(result)
        assert 'b = 1' in result, repr(result)
        assert 'c = 2' in result, repr(result)
    
    def test_globals(self):
        self.break_and_run('int(10)')
        
        result = gdb.execute('cy globals', to_string=True)
        assert '__name__ =' in result, repr(result)
        assert '__doc__ =' in result, repr(result)
        assert 'os =' in result, repr(result)
        assert 'c_var = 12' in result, repr(result)
        assert 'python_var = 13' in result, repr(result)


def _main():
    try:
        gdb.lookup_type('PyModuleObject')
    except RuntimeError:
        msg = ("Unable to run tests, Python was not compiled with "
                "debugging information. Either compile python with "
                "-g or get a debug build (configure with --with-pydebug).")
        warnings.warn(msg)
    else:
        m = __import__(__name__, fromlist=[''])
        tests = inspect.getmembers(m, inspect.isclass)
        
        # test_support.run_unittest(tests)
        
        test_loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            [test_loader.loadTestsFromTestCase(cls) for name, cls in tests])
        
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            os._exit(1)

def main(trace_code=False):
    if trace_code:
        tracer = trace.Trace(count=False, trace=True, outfile=sys.stderr,
                            ignoredirs=[sys.prefix, sys.exec_prefix])
        tracer.runfunc(_main)
    else:
        _main()