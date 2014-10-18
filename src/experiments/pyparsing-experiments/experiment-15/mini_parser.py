#!/usr/bin/env python

# This tries to fix some issues:
# - lines with semi colons were getting jammed together


import sys, math, operator
from pyparsing import *

sys.path.append('../common')
from moccasin_utilities import *


# Accumulators for comments and assignments.

assignments      = {}
comments         = []
expression_stack = []


def init_globals():
    global assignments
    global comments
    global expression_stack
    assignments      = {}
    comments         = []
    expression_stack = []


def pushFirst(strg, loc, toks):
    global expression_stack
    expression_stack.append(toks[0])


def pushUMinus(strg, loc, toks):
    global expression_stack
    if toks and toks[0]=='-':
        expression_stack.append( 'unary -' )


def pushFunction(strg, loc, toks):
    global expression_stack
    print('call ' + toks[0] + '(' + ', '.join(toks[1:]) + ')')
    # expression_stack.append('call ' + toks[0] + '(' + ', '.join(toks[1:]) + ')')


def printMatrixRef(strg, loc, toks):
    global expression_stack
    print('matrix ref ' + toks[0] + '(' + ', '.join(toks[1:]) + ')')


@parse_debug_helper
def print_tokens(tokens):
    # This gets called once for every matching line.
    # Tokens will be a list; the first element will be the variable.
    # For this simple test, we just create a dictionary keyed by the variable.
    global assignments
    global expression_stack
    print tokens
    # assignments[tokens[0]] = eval_stack(expression_stack)
    # expression_stack = []


@parse_debug_helper
def print_delimiter(tokens):
    # This gets called once for every matching line.
    # Tokens will be a list; the first element will be the variable.
    # For this simple test, we just create a dictionary keyed by the variable.
    global assignments
    global expression_stack
    print "delim"
    # assignments[tokens[0]] = eval_stack(expression_stack)
    # expression_stack = []


@parse_debug_helper
def print_comment(tokens):
    global comments
    # Append here ends up mashing all the inputs into the 1st object in the list.
    # For the life of me I can't see what I'm doing wrong.  Forget it for now.
    # comments.append(tokens)
    print tokens


# Map operator symbols to corresponding arithmetic operations.

epsilon = 1e-12

opn = { "+" : operator.add,
        "-" : operator.sub,
        "*" : operator.mul,
        "/" : operator.truediv,
        "^" : operator.pow }

fn  = { "sin" : math.sin,
        "cos" : math.cos,
        "tan" : math.tan,
        "abs" : abs,
        "trunc" : lambda a: int(a),
        "round" : round,
        "sgn" : lambda a: abs(a)>epsilon and cmp(a,0) or 0}

def eval_stack(stack):
    op = stack.pop()
    if op == 'unary -':
        return -eval_stack( stack )
    if op in "+-*/^":
        op2 = eval_stack( stack )
        op1 = eval_stack( stack )
        return opn[op]( op1, op2 )
    elif op == "pi":
        return math.pi # 3.1415926535
    elif op == "E":
        return math.e  # 2.718281828
    elif op in fn:
        return fn[op]( eval_stack( stack ) )
    elif op.isalpha():
        if op in assignments:
            return assignments[op]
        elif op.startswith('call'):
            print op
            return 0
    else:
        return float( op )


# Set up the parser.

EOL                = LineEnd().suppress()
EOS                = LineStart().suppress()
COMMENTSTART       = Literal('%').suppress()
SEMI               = Literal(';').suppress()
COMMA              = Literal(',').suppress()
LBRACKET           = Literal('[').suppress()
RBRACKET           = Literal(']').suppress()
LPAR               = Literal("(").suppress()
RPAR               = Literal(")").suppress()
EQUALS             = Literal('=')
ELLIPSIS           = Literal('...')

ID_BASE            = Word(alphas, alphanums + '_') # [a-zA-Z][_a-zA-Z0-9]*

INTEGER            = Word(nums)
EXPONENT           = Combine(oneOf('E e D d') + Optional(oneOf('+ -')) + Word(nums))
FLOAT              = ( Combine(Word(nums) + Optional('.' + Word(nums)) + EXPONENT)
                     | Combine(Word(nums) + '.' + EXPONENT)
                     | Combine(Word(nums) + '.' + Word(nums))
                     | Combine('.' + Word(nums) + EXPONENT)
                     | Combine('.' + Word(nums))
                    )
NUMBER             = FLOAT | INTEGER

TRUE               = Keyword('true')
FALSE              = Keyword('false')
BOOLEAN            = TRUE | FALSE

# List of references to identifiers.  Used in function definitions.

ID_REF             = ID_BASE.copy()
ARG_LIST           = Group(delimitedList(ID_REF))

# Here begins the grammar for expressions.

EXPR               = Forward()

# Bare matrices.

ROW_WITH_COMMAS    = delimitedList(EXPR)
ROW_WITH_SEMIS     = Optional(ROW_WITH_COMMAS) + SEMI
ROW_WITH_SPACES    = OneOrMore(EXPR)
ROW                = ROW_WITH_SEMIS | ROW_WITH_COMMAS | ROW_WITH_SPACES
BARE_MATRIX        = Group(LBRACKET + ZeroOrMore(ROW) + RBRACKET)

# Function calls and matrix accesses look the same. We will have to
# distinguish them at run-time by figuring out if a given identifier
# reference refers to a function name or a matrix name.  Here I use 2
# grammars because in the case of matrix references (and only in that case),
# you can use a bare ':' in the argument list.

FUNCTION_ID        = ID_BASE.copy()
FUNCTION_ARGS      = delimitedList(EXPR)
FUNCTION_REF       = FUNCTION_ID + LPAR + ZeroOrMore(FUNCTION_ARGS) + RPAR
MATRIX_ID          = ID_BASE.copy()
MATRIX_ARGS        = delimitedList(EXPR | Group(Literal(':')))
MATRIX_REF         = MATRIX_ID + LPAR + ZeroOrMore(MATRIX_ARGS) + RPAR
FUNC_OR_MATRIX_REF = Group(FUNCTION_REF | MATRIX_REF)

# Function handles: http://www.mathworks.com/help/matlab/ref/function_handle.html

FUNC_HANDLE_ID     = ID_BASE.copy()
NAMED_FUNC_HANDLE  = Literal('@') + FUNC_HANDLE_ID
ANON_FUNC_HANDLE   = Literal('@') + LPAR + Group(Optional(ARG_LIST)) + RPAR + EXPR
FUNC_HANDLE        = NAMED_FUNC_HANDLE | ANON_FUNC_HANDLE

# operator precendece in Matlab:
# http://www.mathworks.com/help/matlab/matlab_prog/operator-precedence.html

UNARY_OP           = oneOf('+ - ~')
MATRIX_OP          = oneOf(".' .^ ^")
MULT_OP            = oneOf('* / .* ./ .\\ \\')
PLUS_OP            = oneOf('+ -')
ONE_COLON_OP       = Literal(':')
TWO_COLON_OP       = '::'
COMPARISON_OP      = oneOf('< <= > >= == ~=')
EL_AND_OP          = Literal('&')
EL_OR_OP           = Literal('|')
AND_OP             = Literal('&&')
OR_OP              = Literal('||')

OPERAND            = Group(FUNC_OR_MATRIX_REF | ID_REF | FUNC_HANDLE \
                          | BARE_MATRIX | NUMBER | BOOLEAN)
EXPR              << operatorPrecedence(OPERAND, [
    (UNARY_OP,       1, opAssoc.RIGHT),
    (MATRIX_OP,      2, opAssoc.LEFT),
    (MULT_OP,        2, opAssoc.LEFT),
    (PLUS_OP,        2, opAssoc.LEFT),
    (TWO_COLON_OP,   3, opAssoc.LEFT),
    (ONE_COLON_OP,   2, opAssoc.LEFT),
    (COMPARISON_OP,  2, opAssoc.LEFT),
    (EL_AND_OP,      2, opAssoc.LEFT),
    (EL_OR_OP,       2, opAssoc.LEFT),
    (AND_OP,         2, opAssoc.LEFT),
    (OR_OP,          2, opAssoc.LEFT),
])

COND_EXPR          = operatorPrecedence(OPERAND, [
    (COMPARISON_OP,  2, opAssoc.LEFT),
    (EL_AND_OP,      2, opAssoc.LEFT),
    (EL_OR_OP,       2, opAssoc.LEFT),
    (AND_OP,         2, opAssoc.LEFT),
    (OR_OP,          2, opAssoc.LEFT),
])

ASSIGNED_ID        = ID_BASE.copy()
SIMPLE_ASSIGNMENT  = ASSIGNED_ID + EQUALS + EXPR
MATRIX_ASSIGNMENT  = BARE_MATRIX + EQUALS + EXPR
ASSIGNMENT         = Group(MATRIX_ASSIGNMENT | SIMPLE_ASSIGNMENT)

WHILE_STMT         = Group(Keyword('while') + COND_EXPR)
IF_STMT            = Group(Keyword('if') + COND_EXPR)
ELSEIF_STMT        = Group(Keyword('elseif') + COND_EXPR)
ELSE_STMT          = Keyword('else')
RETURN_STMT        = Keyword('return')
BREAK_STMT         = Keyword('break')
CONTINUE_STMT      = Keyword('continue')
GLOBAL_STMT        = Keyword('global')
PERSISTENT_STMT    = Keyword('persistent')
FOR_ID             = ID_BASE.copy()
FOR_STMT           = Group(Keyword('for') + FOR_ID + EQUALS + EXPR)
SWITCH_STMT        = Group(Keyword('switch') + EXPR)
CASE_STMT          = Group(Keyword('case') + EXPR)
OTHERWISE_STMT     = Keyword('otherwise')
TRY_STMT           = Keyword('try')
CATCH_STMT         = Group(Keyword('catch') + ID_REF)

END                = Keyword('end')

CONTROL_STMT       = WHILE_STMT | IF_STMT | ELSEIF_STMT | ELSE_STMT \
                    | SWITCH_STMT | CASE_STMT | OTHERWISE_STMT \
                    | TRY_STMT | CATCH_STMT \
                    | CONTINUE_STMT | BREAK_STMT | RETURN_STMT \
                    | GLOBAL_STMT | PERSISTENT_STMT | END

SINGLE_VALUE       = ID_REF
IDS_WITH_COMMAS    = delimitedList(SINGLE_VALUE)
IDS_WITH_SPACES    = OneOrMore(SINGLE_VALUE)
MULTIPLE_VALUES    = LBRACKET + (IDS_WITH_SPACES | IDS_WITH_COMMAS) + RBRACKET
FUNCTION_NAME      = ID_BASE.copy()
FUNCTION_LHS       = Optional(Group(MULTIPLE_VALUES | SINGLE_VALUE) + EQUALS)
FUNCTION_ARGS      = Optional(LPAR + ARG_LIST + RPAR)
FUNCTION_DEF_STMT  = Group(Keyword('function') + FUNCTION_LHS + FUNCTION_NAME + FUNCTION_ARGS)

COMMENT            = Group(COMMENTSTART + restOfLine + EOL).setParseAction(print_tokens)
DELIMITER          = COMMA | SEMI
STMT               = (FUNCTION_DEF_STMT | CONTROL_STMT | ASSIGNMENT | EXPR).setParseAction(print_tokens)
MATLAB_SYNTAX      = ZeroOrMore(STMT | DELIMITER | COMMENT)

CONTINUATION       = Combine(ELLIPSIS.leaveWhitespace() + EOL + EOS)
MATLAB_SYNTAX.ignore(CONTINUATION)

MATLAB_SYNTAX.enablePackrat()

# Drivers.

def parse_string(str):
    init_globals()
    result = MATLAB_SYNTAX.parseString(str, parseAll=True)
    return result

def parse_file(filename):
    init_globals()
    result = MATLAB_SYNTAX.parseFile(filename)
    return result


# Direct run interface

if __name__ == '__main__':
    try:
        parse_string("""
a = 3
b = 2 + 3
c = 1.1
d = .1e3
e = 1.e-1
f=.1e-1
g = 5; h = 7;
;
6, 7
% comment line 10
k = a...
+ b
j = 3; k = 33^(2-3)
jj = 3, kk = 455

m = f(1)
n = f(2, 3, 4)
o = [1 2
3 4]
p = a&b
q = (1/2/3) + 56.3
r = f((4+4)-1^3)
% comment line 20
rr = 1  % comment on same line
s = (5 - 66*(4-3)/b)   
[a b] = f(3)
nnn = 1 ...
+ 3
% comment 2
z = 3
% comment 3
x = 3 % comment 4
w = -2 + 3;
a = -2 + (3 * 2^2) + 10
b = 3 ...
+ 2
c = 7; d = 9; e=655
f=(55*10)-4 % yet another comment; with semicolon and ...

g = 1, h = 2,,
i = pi
j = 2 + h
k = foo(2)
m = 2/4
n = 1.1
n = 1.3e4
k = .3e3
p = [3]
q = [3, 4]
r = [44
2]
s = [55, 44;
66 77]
t = [;
8]
u = [1, 2, 3
4 5 6]
v = [9 9 9]
v = [a 9]
[a b] = [c d]
[a, b] = [c d]
[a b] = f(x)
A = [ [333 5 7] ; [2 18 10] ];
B = [ [8 ; 9 ; 16] [7 ; -2 ; -3] ];
D = f(1,2)
E = [11,22,
33,44]
while 1
  x = x - 1
end
if (x > 2)
   x = 1
end
switch a < 10
case 5
   x = 5
otherwise
   x = 10
end

function out = add(a, b)
% adds values of a and b
out = a + b;

function [x y] = foo(r)
x = [r; r]
end

function foo(r)
y
end

function weird
   while 1, x = 3, end
end

function hLine=myplot(x,y,plotColor,markerType)
end

z = @foo
@(x)x*2
@()3

foo = somearray(2,:)
foo = somearray(2, 1:2*3)
foo = somearray(2, 1:2*(a+b))
foo = somearray(2, 1:2*(a+b):10)
b32vecP = permute(b32sumAll, [4 1:3])

f = @(x) 3*x.^2 + 2*x + 7
t = (0:0.001:1);
plot(t,f(t),t,f(2*t),t,f(3*t));
linfunc = @(m,b) @(x) m*x+b;
C2F = linfunc(9/5, 32);
F2C = linfunc(5/9, -32*5/9);

""")
        for name, value in assignments.iteritems():
            print "assign " + name + " = " + str(value)

    except ParseException as err:
        print("error: {0}".format(err))
