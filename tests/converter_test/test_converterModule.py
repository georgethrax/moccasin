#!/usr/bin/env python

from __future__ import print_function
import pytest
import sys
import glob
from pyparsing import ParseResults
sys.path.append('moccasin/converter/')
sys.path.append('moccasin/')
from matlab_parser import *
from converter import *

parser = MatlabGrammar()
#Generates (multiple) parametrized calls to a test function
def pytest_generate_tests(metafunc):
    # called once per each test function
    funcarglist = metafunc.cls.params[metafunc.function.__name__]
    argnames = list(funcarglist[0])
    metafunc.parametrize(argnames, [[funcargs[name] for name in argnames]
            for funcargs in funcarglist],scope='module')

#Parses and converts file to SBML and prints result(output is captured)                       
def build_model(path):
    file = open(path,'r')
    contents = file.read()
    try:
        results = parser.parse_string(contents, print_debug=False, fail_soft=True)
        sbml = create_raterule_model(results, True)
        file.close()
        print(sbml)
    except Exception as e:
        print e
   
#reads file containing expected sbml model and returns it as string
def read_sbml (path):
    file = open(path,'r')
    contents = file.read()
    file.close()
    return contents

# Constructs the params dictionary for test function parametrization
def obtain_params():
    matlab_models=glob.glob("tests/converter_test/converter-test-cases/*.m")
    sbml_models=glob.glob("tests/converter_test/converter-test-cases/*.xml")
    pairs=list()
    for i in range(len(matlab_models)):
        pairs.append((dict(model= matlab_models[i],sbml=sbml_models[i])))
    parameters={'test_converterCases':pairs}
    return parameters

class TestClass:
    # a map specifying multiple argument sets for a test method
    params = obtain_params()
    
    def test_converterCases(self,capsys, model, sbml):
        build_model(model)
        out,err=capsys.readouterr()
        assert out.strip()== read_sbml(sbml).strip()



