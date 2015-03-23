#!/usr/bin/env python
#
# @file    grammar.py
# @brief   Core of the MATLAB grammar definition in PyParsing
# @author  Michael Hucka
#
# <!---------------------------------------------------------------------------
# This software is part of MOCCASIN, the Model ODE Converter for Creating
# Awesome SBML INteroperability. Visit https://github.com/sbmlteam/moccasin/.
#
# Copyright (C) 2014-2015 jointly by the following organizations:
#  1. California Institute of Technology, Pasadena, CA, USA
#  2. Mount Sinai School of Medicine, New York, NY, USA
#  3. Boston University, Boston, MA, USA
#
# This is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation.  A copy of the license agreement is provided in the
# file named "COPYING.txt" included with this software distribution and also
# available online at https://github.com/sbmlteam/moccasin/.
# ------------------------------------------------------------------------- -->


# Basic principles of the parser
# ------------------------------
#
# The main entry point are the functions `MatlabGrammar.parse_string()` and
# `MatlabGrammar.parse_file()`.  There are a few other public methods on
# `MatlabGrammar` for debugging and other tasks, but the basic goal of
# `MatlabGrammar` is to provide the two main entry points.  Both of those
# functions return a data structure that a caller can examine to determine
# what was found in a given MATLAB file.
#
# The data structure returned by the parsing functions is an object of type
# `MatlabContext`.  This object contains a number of fields designed for
# convenient processing by the rest of the MOCCASIN toolchain.  The
# representation of the MATLAB input itself is stored in a field called
# `nodes` inside the `MatlabContext` object.  This field consists of a list
# of `MatlabNode` objects.
#
# Loosely speaking, each `MatlabNode` object represents a MATLAB statement,
# in the order they were read from the input file or string.  The
# `MatlabNode` objects are close to being an Abstract Syntax Tree (AST)
# representation of the input; the main differences are that the nodes
# capture more than only the syntax (they are elaborated by some
# post-processing), and a true AST would put everything into a tree of nodes
# yet our format is currently a mix of lists and nodes.
#
# Hierarchy of `MatlabNode` object classes
# ----------------------------------------
#
# The class `MatlabNode` is defined in the file `matlab.py.  There are
# numerous `MatlabNode` objects and they are hierarchically organized as
# depicted by the following tree diagram.
#
#   MatlabNode
#   |
#   +--Entity
#   |  +- Primitive
#   |  |  +- Number
#   |  |  +- String
#   |  |  +- Boolean
#   |  |  +- Special
#   |  |
#   |  +- Array          # A bare array
#   |  |
#   |  +- Handle
#   |  |  +- FunHandle   # A function handle, e.g., "@foo"
#   |  |  +- AnonFun     # An anonymous function, e.g., "@(x,y)x+y".
#   |  |
#   |  +- Reference
#   |  |  +- Identifier
#   |  |  +- ArrayOrFunCall
#   |  |  +- FunCall
#   |  |  +- ArrayRef
#   |  |  +- StructRef
#   |  |
#   |  +- Operator
#   |     +- UnaryOp
#   |     +- BinaryOp
#   |     +- TernaryOp
#   |     +- Transpose
#   |
#   +--Expression
#   |
#   +--Definition
#   |  +- Assignment
#   |  +- FunDef
#   |
#   +--FlowControl
#   |  +- Try
#   |  +- Catch
#   |  +- Switch
#   |  +- Case
#   |  +- Otherwise
#   |  +- If
#   |  +- Elseif
#   |  +- Else
#   |  +- While
#   |  +- For
#   |  +- End
#   |  +- Branch
#   |
#   +--Command
#   |  +- ShellCommand
#   |  +- MatlabCommand
#   |
#   +- Comment
#
# Roughly speaking, the scheme of things is as follows:
#
# * `Entity` objects represent things that go into `Expression` contents and
#   may also appear in a `MatlabCommand`.  There are a number of subclasses
#   under `Entity`:
#
#    - `Primitive` objects are simple literal values.
#
#    - `Array` objects are also literal values, but are structured.  `Array`
#      objects may be regular arrays or cell arrays.  In this parser and the
#      Python representation, they are treated mostly identically, with only a
#      Boolean attribute (`is_cell`) in `Array` to distinguish them.
#
#    - `Handle` objects in some ways are similar to `Primitive` objects and in
#      other ways similar to `Reference`.  They have an implication of being
#      closures, and consequently may need to be treated specially, so they
#      are grouped into their own subclass.
#
#    - `Reference` objects point to values.  This subclass includes simple
#      variables as well as more complex entities, such as named arrays.
#      Unfortunately, they are among the most difficult objects to classify
#      correctly in MATLAB.  The most notable case involves array accesses
#      versus function calls, which are syntactically identical in MATLAB.
#      `ArrayOrFunCall` objects represent something that cannot be
#      distinguished as either an array reference or a named function call.
#      This problem is discussed in a separate section below.  If the parser
#      *can* infer that a given term is an array reference or a function call,
#      then it will report them as `ArrayRef` or `FunCall`, respectively, but
#      often it is impossible to tell.
#
# * `Expression` objects contain `Entity` objects.  The attribute `content`
#   in an `Expression` object contains a (possibly nested) list of `Entity`
#   objects.
#
# * `Definition` objects have more structure and contain other objects.
#
# * `FlowControl` merits some additional explanation.  In the current parser,
#   flow control statements such as while loops are treated in a very
#   simple-minded way: they are simply objects that store the appearance of
#   the object type, and do not contain the body of the loop or statement.
#   For instance, a `While` object only contains the condition.  The body of
#   the while loop and the `End` statement will appear separately in the list
#   of objects returned by the parser.  (This may change in the future to
#   make the presentation properly nested.)
#
# * `Command` objects are distinguished from other things like function calls
#   either because they have special syntax (`ShellCommand`) or because they
#   are not used to produce a value.  Because MATLAB syntax allows functions
#   to be called without parentheses (that is, you can write `foo arg1 arg1`
#   instead of `foo(arg1, arg2)`), functions called in a "command style" are
#   sometimes returned as `MatlabCommand` rather than `FunCall`.
#
#
# Working with MatlabNode objects
# -------------------------------
#
# Some examples will hopefully provide a better sense for how the
# representation works.  Suppose we have an input file containing this:
#
#   a = 1
#
# `MatlabGrammar.parse_file()` will return a `MatlabContext` object after
# parsing this, and the context object will have one attribute, `nodes`,
# containing a list of `MatlabNode` objects.  In the present case, that list
# will have length `1` because there is only one line in the input.  Here is
# what the list will look like:
#
#   [ Assignment(lhs=Identifier(name='a'), rhs=Number(value='1')) ]
#
# The `Assignment` object has two attributes, `lhs` and `rhs`, representing
# the left-hand and right-hand sides of the assignment, respectively.  Each
# of test attribute values is another `MatlabNode` object.  The object
# representing the variable `a` is
#
#   Identifier(name='a')
#
# This object class has just one attribute, `name`, which can be accessed
# directly to get the value.  Here is an example of how you might do this by
# typing some commands in an interactive Python interpreter:
#
#   (Pdb) x = Identifier(name='a')
#   (Pdb) x.name
#   'a'
#
# Here is another example.  Suppose the input file contains the following:
#
#   a = [1 2]
#
# The parser will return the following node structure:
#
#   [
#   Assignment(lhs=Identifier(name='a'),
#   rhs=Array(is_cell=False, rows=[[Number(value='1'), Number(value='2')]]))
#   ]
#
# The `Array` object has an attribute, `rows`, that stores the rows of the
# array contents as a list of lists.  Each list inside the outermost list
# represents a row.  Thus, in this simple case where there is only one row,
# the value of the `rows` attribute consists of a list containing one list,
# and this one lists contains the representations for the numbers `1` and `2`
# used in the array expression.
#
# And that's basically it.  A caller can take the list of nodes turned by the
# parser and walk down the list one by one, doing whatever processing is
# appropriate for the caller's purposes.  Each time it encounters a
# `MatlabNode` object, it can extract information and process it.  The fact
# that everything is rooted in `MatlabNode` means that callers can use the
# *Visitor* pattern to implement processing algorithms.
#
# A word about the syntax of expressions.  Mathematical and conditional
# expressions returned by the parser are in infix notation (just as in
# MATLAB), but binary operations are grouped left-to-right during parsing and
# appear as sublists.  For example, "1 + 2 + 3" will be returned as:
#
#   [ [ Number(value='1'), BinaryOp(op='+'), Number(value='2') ],
#     BinaryOp(op='+'), Number(value='3') ]
#
#
# Matrices and functions in MATLAB
# --------------------------------
#
# Syntactically, an array access such as `foo(1, 2)` or `foo(x)` looks
# identical to a function call in MATLAB.  This poses a problem for the
# MOCCASIN parser: in many situations it can't tell if something is an array
# or a function, so it can't properly label the object in the parsing
# results.
#
# The way MOCCASIN approaches this problem is the following:
#
# 1. If it can determine unambiguously that something must be an array access
#    based on how it is used syntactically, then it will make it an object of
#    class `ArrayRef`.  Specifically, this is the case when an array access
#    appears on the left-hand side of an assignment statement, and in other
#    situations when the access uses a bare colon character (`:`) for a
#    subscript (because bare colons cannot be used as an argument to a
#    function call).
#
# 2. If it can _infer_ that an object is most likely an array access, it will
#    again make it an `ArrayRef` object.  MOCCASIN does simple type inference
#    by remembering variables it has seen used in assignments.  When those
#    are used in other expressions, MOCCASIN can infer they must be variable
#    names and not function names.
#
# 3. In all other cases, it will label the object `ArrayOrFunCall`.
#
# Users will need to do their own processing when something comes back with
# type `ArrayOrFunCall` to determine what kind of thing the object really is.
# In the most general case, MOCCASIN can't tell from syntax alone whether
# something could be a function, because without running MATLAB (and doing it
# _in the user's environment_, since the user's environment affects the
# functions and scripts that MATLAB knows about), it simply cannot know.
#


# Preface material.
# .............................................................................

from __future__ import print_function
import pdb
import sys
import copy
import pyparsing                        # Need this for version check, so ...
from pyparsing import *                 # ... DON'T merge this & previous stmt!
from distutils.version import LooseVersion
try:
    from grammar_utils import *
    from context import *
    from matlab import *
except:
    from .grammar_utils import *
    from .context import *
    from .matlab import *

# Check minimum version of PyParsing.

if LooseVersion(pyparsing.__version__) < LooseVersion('2.0.3'):
    raise Exception('MatlabGrammar requires PyParsing version 2.0.3 or higher')

# Necessary optimization.  Without this, the PyParsing grammar defined below
# never finishes parsing anything.

ParserElement.enablePackrat()


# MatlabGrammar.
# .............................................................................

class MatlabGrammar:

    # Start of grammar definition.
    #
    # Note: this is written in reverse order, from smallest elements to the
    # highest level parsing object, simply because Python interprets the file
    # in this order and needs each item defined before it encounters it
    # later.  However, for readabily, it's probably easiest to start at the
    # last definition (which is _matlab_syntax) and read up.
    #
    # First, the lowest-level terminal tokens.
    # .........................................................................

    _EOL        = LineEnd().suppress()
    _SOL        = LineStart().suppress()
    _WHITE      = White(ws=' \t')

    _SEMI       = Literal(';').suppress()
    _COMMA      = Literal(',').suppress()
    _LPAR       = Literal("(").suppress()
    _RPAR       = Literal(")").suppress()
    _LBRACKET   = Literal('[').suppress()
    _RBRACKET   = Literal(']').suppress()
    _LBRACE     = Literal('{').suppress()
    _RBRACE     = Literal('}').suppress()
    _EQUALS     = Literal('=').suppress()
    _DOT        = Literal('.').suppress()
    _ELLIPSIS   = Literal('...')

    # This definition of numbers knowingly ignores imaginary numbers because
    # they're not used in our domain.

    _INTEGER    = Word(nums)
    _EXPONENT   = Combine(oneOf('E e D d') + Optional(oneOf('+ -')) + Word(nums))
    _FLOAT      = (Combine(Word(nums) + Optional('.' + Word(nums)) + _EXPONENT)
                   | Combine(Word(nums) + '.' + _EXPONENT)
                   | Combine(Word(nums) + '.' + Word(nums))
                   | Combine('.' + Word(nums) + _EXPONENT)
                   | Combine('.' + Word(nums)))

    # Next come definitions of terminal elements.  The funky syntax with the
    # second parenthesized argument on each line is something PyParsing allows;
    # it's a short form, equivalent to calling .setResultsName(...).

    _NUMBER     = (_FLOAT | _INTEGER)                 ('number')
    _BOOLEAN    = (Keyword('true') | Keyword('false'))('boolean')
    _STRING     = QuotedString("'", escQuote="''")    ('string')

    _ID         = Word(alphas, alphanums + '_')       ('identifier')
    _TILDE      = Literal('~')                        ('tilde')

    _UMINUS     = Literal('-')                        ('unary operator')
    _UPLUS      = Literal('+')                        ('unary operator')
    _UNOT       = Literal('~')                        ('unary operator')
    _TIMES      = Literal('*')                        ('binary operator')
    _ELTIMES    = Literal('.*')                       ('binary operator')
    _MRDIVIDE   = Literal('/')                        ('binary operator')
    _MLDIVIDE   = Literal('\\')                       ('binary operator')
    _RDIVIDE    = Literal('./')                       ('binary operator')
    _LDIVIDE    = Literal('.\\')                      ('binary operator')
    _MPOWER     = Literal('^')                        ('binary operator')
    _ELPOWER    = Literal('.^')                       ('binary operator')
    _PLUS       = Literal('+')                        ('binary operator')
    _MINUS      = Literal('-')                        ('binary operator')
    _LT         = Literal('<')                        ('binary operator')
    _LE         = Literal('<=')                       ('binary operator')
    _GT         = Literal('>')                        ('binary operator')
    _GE         = Literal('>=')                       ('binary operator')
    _EQ         = Literal('==')                       ('binary operator')
    _NE         = Literal('~=')                       ('binary operator')
    _AND        = Literal('&')                        ('binary operator')
    _OR         = Literal('|')                        ('binary operator')
    _SHORT_AND  = Literal('&&')                       ('binary operator')
    _SHORT_OR   = Literal('||')                       ('binary operator')

    # Operators that have special-case handling.

    _COLON      = Literal(':')
    _NC_TRANSP  = Literal(".'")
    _CC_TRANSP  = Literal("'")

    # Basic keywords.

    _BREAK      = Keyword('break')                     ('keyword')
    _CASE       = Keyword('case')                      ('keyword')
    _CATCH      = Keyword('catch')                     ('keyword')
    _CLASSDEF   = Keyword('classdef')                  ('keyword')
    _CONTINUE   = Keyword('continue')                  ('keyword')
    _ELSE       = Keyword('else')                      ('keyword')
    _ELSEIF     = Keyword('elseif')                    ('keyword')
    _END        = Keyword('end')                       ('keyword')
    _FOR        = Keyword('for')                       ('keyword')
    _FUNCTION   = Keyword('function')                  ('keyword')
    _GLOBAL     = Keyword('global')                    ('keyword')
    _IF         = Keyword('if')                        ('keyword')
    _OTHERWISE  = Keyword('otherwise')                 ('keyword')
    _PARFOR     = Keyword('parfor')                    ('keyword')
    _PERSISTENT = Keyword('persistent')                ('keyword')
    _RETURN     = Keyword('return')                    ('keyword')
    _SWITCH     = Keyword('switch')                    ('keyword')
    _TRY        = Keyword('try')                       ('keyword')
    _WHILE      = Keyword('while')                     ('keyword')

    # Grammar for expressions.
    #
    # Some up-front notes:
    #
    # 1) Calling PyParsing's setResultsName() function or its equivalent
    # yields A COPY of the thing affected -- it does not return the original
    # thing.  This means that if you have a grammar element of the form
    #             _foo = Group(_bar('bar') | _biff('biff'))
    # then _bar and _biff never actually get invoked when _foo is invoked;
    # what get invoked are copies of _bar and _biff, because that's what gets
    # stored in _foo.  This has implications for our use of _store_stmt()
    # further below: setting a name on _bar & _biff as above causes copies of
    # _bar and _biff to be used in _foo, which means _bar and _biff never get
    # called, which means _store_stmt() is never called either.  This leads
    # to subtle and frustrating rounds of bug-chasing.
    #
    # 2) The grammar below is sometimes designed with the assumption that the
    # input is valid Matlab.  This fits our purpose, which is to parse valid
    # Matlab, so we can afford to produce something simpler here and assume
    # that the input won't do some things that the Matlab parser would
    # reject.  The basic rule is: accept everything that's valid Matlab, but
    # don't worry about deliberately excluding what isn't valid Matlab.
    # .........................................................................

    _expr          = Forward()

    # The possible statement separators/delimiters in Matlab are:
    #   - EOL
    #   - line comment (because they eat the EOL at the end)
    #   - block comments
    #   - semicolon
    #   - comma
    # We handle EOL implicitly in most cases by leaving PyParsing's default
    # whitespace definition as-is, which marks EOL as an ignored whitespace
    # character. However, sometimes Matlab syntax requires special care with
    # EOL, so in those cases, EOL is handled explicitly.

    _delimiter     = _COMMA | _SEMI

    _line_c_start  = Literal('%').suppress()
    _block_c_start = Literal('%{').suppress()
    _block_c_end   = Literal('%}').suppress()
    _line_comment  = Group(_line_c_start + restOfLine + _EOL)
    _block_comment = Group(_block_c_start + SkipTo(_block_c_end, include=True))
    _comment       = Group(_block_comment('comment') | _line_comment('comment'))

    # Comma-separated arguments to matrix/array/cell arrays can have ':'
    # in arguments, but arguments to function calls can't.  Parameter lists in
    # some other situations (like function return values) can have '~', but
    # the other elements can only be identifiers, not expressions.  The
    # following are the different versions used in different places later on.
    #
    # For this to work, the next bunch of grammar objects have to be
    # constructed with different whitespace-handling rules: they must not eat
    # line breaks, because we need to match EOL explicitly, or else we can't
    # properly parse a matrix like the following as consisting of 2 rows:
    #    a = [1 2
    #         3 4]
    # That's the reason for the next call to setDefaultWhitespaceChars().
    # This is turned off again further below.

    ParserElement.setDefaultWhitespaceChars(' \t')

    _one_sub       = Group(_COLON('colon')) | _expr
    _comma_subs    = _one_sub + ZeroOrMore(_COMMA + _one_sub)
    _space_subs    = _one_sub + ZeroOrMore(_WHITE.suppress() + _one_sub).leaveWhitespace()
    _call_args     = delimitedList(_expr)
    _fun_params    = delimitedList(Group(_TILDE) | Group(_ID))

    # Bare matrices.  This is a cheat because it doesn't check that all the
    # element contents have the same data type.  But again, since we expect our
    # input to be valid Matlab, we don't expect to have to verify that property.

    _row_sep       = _SEMI | _EOL | _comment.suppress()
    _one_row       = _comma_subs('subscript list') ^ _space_subs('subscript list')
    _rows          = Group(_one_row) + ZeroOrMore(_row_sep + Group(_one_row))
    _bare_array    = Group(_LBRACKET + Optional(_rows('row list')) + _RBRACKET
                          ).setResultsName('array')  # noqa

    ParserElement.setDefaultWhitespaceChars(' \t\n\r')

    # Cell arrays.  You can write {} by itself, but a reference has to have at
    # least one subscript: "somearray{}" is not valid.  Newlines don't
    # seem to be allowed in args to references, but a bare ':' is allowed.
    # Now the hard parts:
    # - The following parses as a cell reference:        a{1}
    # - The following parses as a function call:         a {1}
    # - The following parses as an array of 3 elements:  [a {1} a]

    _bare_cell     = Group(_LBRACE + ZeroOrMore(_rows('row list')) + _RBRACE
                          ).setResultsName('cell array')  # noqa
    _cell_name     = Group(_ID)
    _cell_args     = Group(_comma_subs)
    _cell_access   = Group(_cell_name('name') + _LBRACE.leaveWhitespace()
                           + _cell_args('subscript list') + _RBRACE
                          ).setResultsName('cell array')  # noqa
    _cell_array    = _cell_access | _bare_cell

    # Matlab functions can be called with arguments that are either
    # surrounded with parentheses or not.  In other words, if you define a
    # function 'f' in a file, you can actually call it two ways:
    #    f(x)
    #    f x
    # Spaces are allowed in the first form too:
    #    f (x)
    # Unfortunately, the forms using parentheses look identical to matrix/array
    # accesses, and there's no way to tell them apart except by determining
    # whether 'f' is a function or command.  This means it's ultimately
    # run-time dependent, and depends on the functions and scripts that the
    # user has defined.  In the case of array references, you can use bare
    # ':' in the argument list.  This means that if a ':' is found, it's an
    # array reference for sure -- possibly helping to disambiguate some
    # cases.  For that reason, the ':' case is split out below.  Not yet sure
    # if it's worth it.
    #
    # Another complication: you can put function names or arrays inside a
    # cell array, reference into that array to get the function, and hand it
    # arguments.  E.g.:
    #    x = fcnArray{3}(y)
    #    x = somearray{1}(2,3)
    # That's the reason for _cell_access in the definition of _fun_access.

    _fun_arr_name     = Group(_ID)
    _fun_access       = _fun_arr_name ^ Group(_cell_access('cell array'))
    _funcall_or_array = Group(_fun_access('name')
                              + _LPAR + _call_args('argument list') + _RPAR
                             ).setResultsName('array or function')  # noqa

    _array_name       = Group(_ID)
    _array_args       = Group(_comma_subs)
    _array_access     = Group(_array_name('name')
                              + _LPAR + _array_args('subscript list') + _RPAR
                             ).setResultsName('array')  # noqa

    # Handles: http://www.mathworks.com/help/matlab/ref/function_handle.html
    # In all function arguments, you can use a bare tilde to indicate a value
    # that can be ignored.  This is not obvious from the functional
    # documentation, but it seems to be the case when I try it.  (It's the
    # case for function defs and function return values too.)

    _handle_name   = Group(_ID)
    _named_handle  = Group('@' + _handle_name('name'))
    _anon_handle   = Group('@' + _LPAR + _call_args('argument list')
                           + _RPAR + _expr('function definition'))  # noqa
    _fun_handle    = (_named_handle | _anon_handle).setResultsName('function handle')

    # Struct array references.  This is incomplete: in Matlab, the LHS can
    # actually be a full expression that yields a struct.  Here, to avoid an
    # infinitely recursive grammar, we only allow a specific set of objects
    # and exclude a full expr.  (Doing the obvious thing, expr + "." + _ID,
    # results in an infinitely-recursive grammar.)  Also note _bare_array is
    # deliberately not part of the following because [1].foo is not legal.

    _struct_base   = Group(_cell_access
                           | _funcall_or_array
                           | _array_access
                           | _fun_handle
                           | _ID)
    _struct_field  = Group(_ID) | Group(_LPAR + _expr + _RPAR)
    _struct_access = Group(_struct_base('struct base')
                           + _DOT.leaveWhitespace() + _struct_field('field')
                          ).setResultsName('struct')  # noqa

    # The transpose operator is a problem.  You can actually apply it to full
    # expressions, but I haven't been able to write a grammar that doesn't
    # lead to infinite recursion.  This hacky thing is a partial solution.

    _paren_expr    = _LPAR + _expr + _RPAR
    _transp_what   = _paren_expr ^ Group(_cell_array
                                         | _funcall_or_array
                                         | _bare_array
                                         | _array_access
                                         | _fun_handle
                                         | _BOOLEAN | _ID | _NUMBER | _STRING)
    _transpose_op  = _NC_TRANSP ^ _CC_TRANSP
    _transpose     = Group(_transp_what('operand') + _transpose_op('operator')
                          ).setResultsName('transpose')  # noqa

    # And now, general expressions and operators.

    _operand       = Group(_struct_access
                           | _transpose
                           | _bare_array
                           | _funcall_or_array
                           | _array_access
                           | _cell_array
                           | _fun_handle
                           | _BOOLEAN | _ID | _NUMBER | _STRING)

    # The operator precedence rules in Matlab are listed here:
    # http://www.mathworks.com/help/matlab/matlab_prog/operator-precedence.html

    # FIXME: colon operator for 3 arguments is wrong.

    _plusminus     = _PLUS ^ _MINUS
    _uplusminusneg = _UMINUS ^ _UPLUS ^ _UNOT
    _timesdiv      = _TIMES ^ _ELTIMES ^ _MRDIVIDE ^ _MLDIVIDE ^ _RDIVIDE ^ _LDIVIDE
    _power         = _MPOWER ^ _ELPOWER
    _logical_op    = _LT ^ _LE ^ _GT ^ _GE ^ _EQ ^ _NE
    _colon_op      = _COLON('colon operator')

    _expr          << infixNotation(_operand, [
        (Group(_uplusminusneg), 1, opAssoc.RIGHT),
        (Group(_power),         2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_timesdiv),      2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_plusminus),     2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_colon_op),      2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_logical_op),    2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_AND),           2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_OR),            2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_SHORT_AND),     2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_SHORT_OR),      2, opAssoc.LEFT, makeLRlike(2)),
    ])

    _cond_expr     = infixNotation(_operand, [
        (Group(_logical_op),    2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_AND),           2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_OR),            2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_SHORT_AND),     2, opAssoc.LEFT, makeLRlike(2)),
        (Group(_SHORT_OR),      2, opAssoc.LEFT, makeLRlike(2)),
    ])

    # Assignments.  We tag the LHS with 'lhs' whether it's a single variable
    # or an array, because we can distinguish the cases by examining the
    # parsed object.

    _lhs_var        = Group(_ID)
    _simple_assign  = Group(_lhs_var('lhs') + _EQUALS + _expr('rhs'))
    _lhs_array      = Group(_bare_array | _array_access | _cell_array | _struct_access)
    _other_assign   = Group(_lhs_array('lhs') + _EQUALS + _expr('rhs'))
    _assignment     = (_other_assign | _simple_assign).setResultsName('assignment')

    # Control statements.
    # FIXME: this should be full statements, not line-oriented.

    _cond           = Group(_cond_expr)                   ('condition')
    _while_stmt     = Group(_WHILE + _cond)               ('while statement')
    _if_stmt        = Group(_IF + _cond)                  ('if statement')
    _elseif_stmt    = Group(_ELSEIF + _cond)              ('elseif statement')
    _loop_var       = Group(_ID)                          ('loop variable')
    _for_stmt       = Group(_FOR + _loop_var + _EQUALS
                            + _expr('expression'))        ('for statement')
    _switch_stmt    = Group(_SWITCH + _expr('expression'))('switch statement')
    _case_stmt      = Group(_CASE + _expr('expression'))  ('case statement')
    _catch_var      = Group(_ID)                          ('catch variable')
    _catch_stmt     = Group(_CATCH + _catch_var)          ('catch statement')
    _otherwise_stmt = _OTHERWISE                          ('otherwise statement')
    _else_stmt      = _ELSE                               ('else statement')
    _end_stmt       = _END                                ('end statement')
    _try_stmt       = _TRY                                ('try statement')
    _continue_stmt  = _CONTINUE                           ('continue statement')
    _break_stmt     = _BREAK                              ('break statement')
    _return_stmt    = _RETURN                             ('return statement')

    _control_stmt   = Group(_while_stmt
                            | _if_stmt
                            | _else_stmt
                            | _elseif_stmt
                            | _switch_stmt
                            | _case_stmt
                            | _otherwise_stmt
                            | _for_stmt
                            | _try_stmt
                            | _catch_stmt
                            | _continue_stmt
                            | _break_stmt
                            | _return_stmt
                            | _end_stmt
                           ).setResultsName('control statement')  # noqa

    # When a function returns multiple values and the LHS is an array
    # expression in square brackets, a bare tilde can be put in place of an
    # argument value to indicate that the value is to be ignored.

    _single_value   = Group(_ID) | Group(_TILDE)
    _comma_values   = delimitedList(_single_value)
    _space_values   = OneOrMore(_single_value)
    _multi_values   = _LBRACKET + (_comma_values ^ _space_values) + _RBRACKET
    _fun_outputs    = Group(_multi_values) | Group(_single_value)
    _fun_paramslist = _LPAR + Optional(_fun_params) + _RPAR
    _fun_name       = Group(_ID)
    _fun_def_stmt   = Group(_FUNCTION.suppress()
                            + Optional(_fun_outputs('output list') + _EQUALS())
                            + _fun_name('name')
                            + Optional(_fun_paramslist('parameter list'))
                           ).setResultsName('function definition')  # noqa

    # Shell commands don't respect ellipses or delimiters, so we use EOL
    # explicitly here and match _shell_cmd at the _matlab_syntax level.

    _shell_cmd_cmd  = Group(restOfLine)('command')
    _shell_cmd      = Group(Group('!' + _shell_cmd_cmd + _EOL)('shell command'))

    # Matlab commands (and function calls) can take unquoted arguments:
    #    clear x y z
    #    format long
    #    print -dpng magicsquare.png
    #    save /tmp/foo
    #    save relative/path.m
    # etc.  The same ones take other forms, like pause(n), but those will get
    # caught by the regular function call grammar.  They respect delimiters.
    #
    # Some of these are problematic.  Consider a command line like this:
    #    save /tmp/foo
    # This actually looks exactly like
    #    a/b/c
    # i.e., a numerical expression, with the '/' meaning division, and
    # "save", "tmp" and "foo" being variables.  I'm not even sure how Matlab
    # does it.  The cases could be differentiated based on knowing that "save"
    # is a command, but then you would have to know about all commands that
    # can take file paths as arguments.  Doable, but fragile -- some future
    # Matlab version could introduce more such commands.
    #
    # The grammar below probably doesn't handle all cases correctly because I
    # haven't figured out what to do about this.  This relies on the order in
    # which it's attempted to be matched by _stmt, and in particular, this has
    # to be tried before trying _expr.

    ParserElement.setDefaultWhitespaceChars(' \t')

    _noncmd_start = _EQUALS | _LPAR | _delimiter | _comment
    _cmd_name     = Group(_ID)
    _cmd_args     = Group(SkipTo(_delimiter | _comment | _EOL))
    _cmd_stmt     = Group(_cmd_name('name')
                          + FollowedBy(_WHITE + NotAny(_noncmd_start))
                          + _cmd_args('arguments')
                         ).setResultsName('command statement')  # noqa

    ParserElement.setDefaultWhitespaceChars(' \t\n\r')

    # The definition of _stmt puts all statement types except _shell_cmd
    # together and sets them up to allow ellipsis continuations.

    _continuation  = Combine(_ELLIPSIS.leaveWhitespace() + _EOL + _SOL)

    _stmt = Group(_fun_def_stmt | _control_stmt | _assignment
                  | _cmd_stmt | _expr('expression'))
    _stmt.ignore(_continuation)

    # Finally, we bring it all together into the root element of our grammar.

    _matlab_syntax = ZeroOrMore(_stmt ^ _shell_cmd ^ _delimiter ^ _comment)


    # Generator for final MatlabNode-based output representation.
    #
    # The following post-processes the ParseResults-based output from PyParsing
    # and creates our format consisting of MatlabContext and MatlabNode.
    # .........................................................................

    def _generate_nodes_and_contexts(self, pr):
        # 1st pass: visit ParseResults items and translate them to MatlabNodes.
        nodes = [self._transform_pr(item) for item in pr]
        # 2nd pass: create contexts, save variable assignments and function
        # calls, and try to convert references if we can.  Since we may need
        # to replace nodes, we create a new list of nodes here.
        self._push_context(MatlabContext('(outermost context)'))
        self._context.nodes = [self._process_node(node) for node in nodes]
        return self._context


    def _process_node(self, node):
        if not isinstance(node, MatlabNode):
            return node
        if isinstance(node, Assignment):
            self._save_assignment(node)
        elif isinstance(node, FunDef):
            self._push_context(self._save_function_definition(node))
        elif isinstance(node, End):
            # FIXME while/for/etc. loops will have end statements too!
            self._pop_context()
        self._save_inferred_type(node)
        self._save_calls(node)
        return self._convert_types(node)


    # This function modifies the current context.
    def _save_inferred_type(self, node):
        if not isinstance(node, MatlabNode):
            return
        if isinstance(node, Assignment):
            lhs = node.lhs
            rhs = node.rhs
            if isinstance(lhs, Identifier):
                if isinstance(rhs, Array) or isinstance(rhs, Number) or \
                   isinstance(rhs, Boolean) or isinstance(rhs, String):
                    self._save_type(lhs.name, 'variable')
            elif isinstance(lhs, ArrayRef):
                self._save_type(lhs.name, 'variable')
            elif isinstance(lhs, Array):
                # If the LHS of an assignment is a bare array, and if there
                # are bare identifiers inside the array, then they must be
                # variables and not functions (else, syntax error).
                row = lhs.rows[0]  # Can only have one row when arrays is on lhs.
                for item in row:
                    if isinstance(item, Identifier):
                        self._save_type(item.name, 'variable')
        elif isinstance(node, FunDef):
            if node.output:
                for var in node.output:
                    # The output parameter names are a safe bet to assume to
                    # be variables.
                    if isinstance(var, Identifier):
                        self._save_type(var.name, 'variable')
            if node.parameters:
                for param in node.parameters:
                    # FIXME this labels parameters as variables, but the
                    # parameter could be a function name or handle when it's
                    # called.  Need to correlate what's done here with the
                    # arguments used in the call to the function.
                    if isinstance(param, Identifier):
                        self._save_type(param.name, 'variable')


    def _save_calls(self, node):
        if isinstance(node, ArrayOrFunCall):
            found_type = self._get_type(node.name, self._context, False)
            if found_type != 'variable':
                self._save_function_call(node)
        elif isinstance(node, Assignment):
            self._save_calls(node.rhs)
        elif isinstance(node, FunCall):
            self._save_function_call(node)
        elif isinstance(node, ArrayRef):
            for arg in node.args:
                self._save_calls(arg)
        elif isinstance(node, Array):
            for row in node.rows:
                self._save_calls(row)
        elif isinstance(node, AnonFun):
            self._save_calls(node.body)
        elif isinstance(node, Expression):
            for thing in node.content:
                self._save_calls(thing)
        elif isinstance(node, list):
            for thing in node:
                self._save_calls(thing)


    # This function assumes that it is being executed in the context for
    # which it makes sense to do the conversion.  Currently this means it
    # assumes it's being executed by _process_node() 
    def _convert_types(self, node):
        # For now, this is pretty limited.
        if isinstance(node, list):
            return [self._convert_types(item) for item in node]
        elif isinstance(node, ArrayOrFunCall):
            # FIXME: it is potentially the case that a function call is
            # nested, using the result of another reference.  In that case,
            # the name won't be an Identifier.  Currently, the code below
            # doesn't handle that case.
            if not isinstance(node.name, Identifier):
                return node
            the_name = node.name.name
            if self._get_type(the_name, self._context, True) == 'variable':
                # We have seen this name before, and it's not a function.  We
                # can convert this ArrayOrFunCall to an ArrayRef.  We can
                # also convert the arguments/subscripts.
                the_args = self._convert_types(node.args)
                node = ArrayRef(name=node.name, args=the_args, is_cell=False)

                # Also, if it was previously unknown whether this name is a
                # function or array, it might have been put in the list of
                # function calls.  Remove it if so.
                if the_name in self._context.calls:
                    self._context.calls.pop(the_name)
            else:
                # Although we didn't change the type of this ArrayOrFunCall,
                # we may still be able to change some of its arguments.
                node.args = self._convert_types(node.args)

        elif isinstance(node, FunCall) or isinstance(node, ArrayRef):
            # Convert the arguments or subscripts.
            node.args = self._convert_types(node.args)

        elif isinstance(node, Array):
            node.rows = [self._convert_types(row) for row in node.rows]

        elif isinstance(node, StructRef):
            # A structure reference may have a complicated "name" part.
            node.name = self._convert_types(node.name)

        elif isinstance(node, AnonFun):
            node.args = self._convert_types(node.args)
            node.body = self._convert_types(node.body)

        elif isinstance(node, Expression):
            node.content = self._convert_types(node.content)

        elif isinstance(node, Assignment):
            node.lhs = self._convert_types(node.lhs)
            node.rhs = self._convert_types(node.rhs)

        elif isinstance(node, Switch) or isinstance(node, Case) \
             or isinstance(node, If) or isinstance(node, Elseif) \
             or isinstance(node, While):
            node.cond = self._convert_types(node.cond)

        elif isinstance(node, For):
            node.expr = self._convert_types(node.expr)

        elif isinstance(node, Transpose):
            node.operand = self._convert_types(node.operand)

        # Make sure this is the final statement!
        return node


    # Transform ParseResults to MatlabNode-based output format.
    #
    # This takes our heavily-annotated output from PyParsing and converts it
    # to a tree-based representation consisting of lists of MatlabNode objects.
    # It's called from _generate_nodes_and_contexts().
    #
    # In what follows, the _transform_* functions are visitors named after
    # the names of our grammar objects (in the PyParsing grammar definition
    # above).  They are named such that the function _transform_pr() can
    # dispatch on the name we assign to PyParsing objects.  For instance, to
    # format what we label an "assignment" in the PyParsing grammar above,
    # there's a function called _transform_assignment() below.
    # .........................................................................

    def _transform_pr(self, pr):
        if len(pr) > 1 and not nonempty_dict(pr):
            # It's a list of items.  Return a list.
            return [self._transform_pr(item) for item in pr]
        if not nonempty_dict(pr):
            # It's an expression.
            return self._transform_expression(pr)
        # Not an expression, but an individual, single parse result.
        # We dispatch to the appropriate transformer by building the name.
        key = first_key(pr)
        func_name = '_transform_' + '_'.join(key.split())
        if func_name in MatlabGrammar.__dict__:
            return MatlabGrammar.__dict__[func_name](self, pr)
        else:
            self._warn('Internal error: no transformer for ' + str(key))


    def _transform_identifier(self, pr):
        return Identifier(name=pr['identifier'])


    def _transform_number(self, pr):
        return Number(value=pr['number'])


    def _transform_boolean(self, pr):
        return Boolean(value=pr['boolean'])


    def _transform_string(self, pr):
        return String(value=pr['string'])


    def _transform_tilde(self, pr):
        return Special(value='~')


    def _transform_end(self, pr):
        return Special(value='end')


    def _transform_end_statement(self, pr):
        return End()


    def _transform_colon(self, pr):
        return Special(value=':')


    def _transform_colon_operator(self, pr):
        return TernaryOp(op=':')


    def _transform_unary_operator(self, pr):
        op_key = first_key(pr)
        return UnaryOp(op=pr[op_key])


    def _transform_binary_operator(self, pr):
        op_key = first_key(pr)
        return BinaryOp(op=pr[op_key])


    def _transform_transpose(self, pr):
        content = pr['transpose']
        the_op = content['operator']
        the_operand = self._transform_pr(content['operand'])
        return Transpose(op=the_op, operand=the_operand)


    def _transform_expression(self, pr):
        return Expression(content=[self._transform_pr(thing) for thing in pr])


    def _transform_assignment(self, pr):
        content = pr['assignment']
        lvalue = self._transform_pr(content['lhs'])
        rvalue = self._transform_pr(content['rhs'])
        node = Assignment(lhs=lvalue, rhs=rvalue)
        return node


    def _transform_array(self, pr):
        content = pr['array']
        # Two kinds of array situations: a bare array, and one where we
        # managed to determine it's an array access (and not the more
        # ambiguous function call or array access).  If we have a name, it's
        # the latter; if we have a row list, it's the former.
        if 'name' in content:
            # Array reference.
            the_name = self._transform_pr(content['name'])
            the_subscripts = self._convert_list(content['subscript list'])
            return ArrayRef(name=the_name, args=the_subscripts, is_cell=False)
        elif 'row list' in content:
            # Bare array.
            return Array(rows=self._convert_rows(content['row list']), is_cell=False)
        else:
            # No row list or subscript list => empty array.
            return Array(rows=[], is_cell=False)


    def _transform_cell_array(self, pr):
        content = pr['cell array']
        # This is basically like regular arrays.  Again, two kinds of
        # situations: a bare cell array, and one where we managed to
        # determine it's an array access.  If we have a name, it's the
        # latter; if we have a row list, it's the former.
        if 'name' in content:
            # Array reference.
            the_name = self._transform_pr(content['name'])
            the_subscripts = self._convert_list(content['subscript list'])
            return ArrayRef(name=the_name, args=the_subscripts, is_cell=True)
        elif 'row list' in content:
            # Bare array.
            return Array(rows=self._convert_rows(content['row list']), is_cell=True)
        else:
            # No row list or subscript list => empty array.
            return Array(rows=[], is_cell=True)


    def _transform_array_or_function(self, pr):
        content = pr['array or function']
        the_name = self._transform_pr(content['name'])
        the_args = self._convert_list(content['argument list'])
        return ArrayOrFunCall(name=the_name, args=the_args)


    def _transform_function_handle(self, pr):
        content = pr['function handle']
        if 'name' in content:
            return FunHandle(name=self._transform_pr(content['name']))
        else:
            the_args = self._convert_list(content['argument list'])
            the_body = self._transform_pr(content['function definition'])
            return AnonFun(args=the_args, body=the_body)


    def _transform_function_definition(self, pr):
        content = pr['function definition']
        the_name = self._transform_pr(content['name'])
        if 'parameter list' in content:
            the_params = self._convert_list(content['parameter list'])
        else:
            the_params = None
        if 'output list' in content:
            the_output = self._convert_list(content['output list'])
        else:
            the_output = None
        # FIXME should get the body somehow
        the_body = None
        return FunDef(name=the_name, parameters=the_params,
                      output=the_output, body=the_body)


    def _transform_struct(self, pr):
        content = pr['struct']
        the_base = self._transform_pr(content['struct base'])
        the_field = self._transform_pr(content['field'])
        return StructRef(name=the_base, field=the_field)


    def _transform_shell_command(self, pr):
        content = pr['shell command']
        the_command = self._transform_pr(content['command'][0])
        return ShellCommand(command=the_command)


    def _transform_command_statement(self, pr):
        content = pr['command statement']
        the_name = self._transform_pr(content['name'])
        the_args = content['arguments'][0]
        return MatlabCommand(command=the_name, args=the_args)


    def _transform_comment(self, pr):
        return Comment(content=pr['comment'][0])


    def _transform_control_statement(self, pr):
        content = pr['control statement']
        return self._transform_pr(content)


    def _transform_while_statement(self, pr):
        content = pr['while statement']
        the_cond = self._transform_pr(content['condition'])
        return While(cond=the_cond)


    def _transform_if_statement(self, pr):
        content = pr['if statement']
        the_cond = self._transform_pr(content['condition'])
        return If(cond=the_cond)


    def _transform_elseif_statement(self, pr):
        content = pr['elseif statement']
        the_cond = self._transform_pr(content['condition'])
        return Elseif(cond=the_cond)


    def _transform_else_statement(self, pr):
        return Else()


    def _transform_switch_statement(self, pr):
        content = pr['switch statement']
        the_cond = self._transform_pr(content['expression'])
        return Switch(cond=the_cond)


    def _transform_case_statement(self, pr):
        content = pr['case statement']
        the_cond = self._transform_pr(content['expression'])
        return Case(cond=the_cond)


    def _transform_otherwise_statement(self, pr):
        return Otherwise()


    def _transform_for_statement(self, pr):
        content = pr['for statement']
        the_var = self._transform_pr(content['loop variable'])
        the_expr = self._transform_pr(content['expression'])
        return For(var=the_var, expr=the_expr)


    def _transform_try_statement(self, pr):
        return Try()


    def _transform_catch_statement(self, pr):
        content = pr['catch statement']
        the_var = self._transform_pr(content['catch variable'])
        return Catch(var=the_var)


    def _transform_end_statement(self, pr):
        return End()


    def _transform_break(self, pr):
        return Branch(kind='break')


    def _transform_return(self, pr):
        return Branch(kind='return')


    def _transform_continue(self, pr):
        return Branch(kind='continue')


    def _transform_shell_command(self, pr):
        return ShellCommand(command=pr['shell command'][1][0])


    def _convert_list(self, list):
        return [self._transform_pr(thing) for thing in list]


    def _convert_rows(self, rowlist):
        return [self._convert_list(row['subscript list']) for row in rowlist]


    # Context and scope management.
    #
    # This block is used by the code that converts the PyParsing output to
    # our MatlabNode and MatlabContext objects.
    #
    # In Matlab, an input file will be either a script, or a function
    # definition.  A script is distinguished from a function definition
    # simply by not starting with "function ..." (after any initial comments
    # in the file).
    #
    # Files can contain nested function definitions.  Each of these sets up
    # contexts, inside of which can be more matlab commands such as variable
    # assignments.  For our purposes, we're particularly interested in
    # tracking functions, so the context-tracking scheme in grammar.py is
    # organized around function definitions.
    #
    # To track nested function definitions, grammar.py has a notion of
    # "contexts".  A context is a MatlabContext object.  It holds the
    # functions, variables and other definitions found at a particular level
    # of nesting.
    #
    # The outermost context is the file.  Each time a new function
    # declaration is encountered, another context is "pushed".  The context
    # pushed is a MatlabContext object containing the parsing results
    # returned for a function definition.  When an 'end' statement is
    # encountered in the input, the current context is popped.
    # .........................................................................

    def _push_context(self, newcontext):
        newcontext.parent = self._context
        self._context = newcontext


    def _pop_context(self):
        # Don't pop top-most context.
        if self._context.parent:
            self._context = self._context.parent


    def _duplicate_context(self, dest):
        if not dest.context:
            dest.context = copy.copy(self._context)


    def _save_function_definition(self, node):
        # FIXME function "names" might be more than identifiers, such as a
        # cell array or structure reference.  This doesn't handle that.
        if isinstance(node.name, Identifier):
            the_name = node.name.name
        else:
            the_name = node.name
        newcontext = MatlabContext(name=the_name, parent=self._context,
                                   parameters=node.parameters,
                                   returns=node.output, pr=None)
        self._context.functions[the_name] = newcontext
        return newcontext


    def _save_assignment(self, node):
        key = MatlabGrammar.make_key(node.lhs)
        self._context.assignments[key] = node.rhs


    def _save_function_call(self, node):
        key = MatlabGrammar.make_key(node.name)
        self._context.calls[key] = node.args


    def _save_type(self, thing, type):
        key = MatlabGrammar.make_key(thing)
        self._context.types[key] = type


    def _get_type(self, thing, context, recursive=False):
        key = MatlabGrammar.make_key(thing)
        if key in context.types:
            return context.types[key]
        elif recursive and hasattr(context, 'parent') and context.parent:
            return self._get_type(key, context.parent, True)
        else:
            return None


    # Debugging.
    # .........................................................................

    # Name each grammar object after itself, so that when PyParsing prints
    # debugging output, it uses the name rather than a generic regexp term.

    _to_name = [_AND, _BOOLEAN, _BREAK, _CASE, _CATCH, _CC_TRANSP, _CLASSDEF,
                _COLON, _COMMA, _CONTINUE, _DOT, _ELLIPSIS, _ELPOWER, _ELSE,
                _ELSEIF, _ELTIMES, _END, _EOL, _EQ, _EQUALS, _EXPONENT,
                _FLOAT, _FOR, _FUNCTION, _GE, _GLOBAL, _GT, _ID, _IF,
                _INTEGER, _LBRACE, _LBRACKET, _LDIVIDE, _LE, _LPAR, _LT,
                _MINUS, _MLDIVIDE, _MPOWER, _MRDIVIDE, _NC_TRANSP, _NE,
                _NUMBER, _OR, _OTHERWISE, _PARFOR, _PERSISTENT, _PLUS,
                _RBRACE, _RBRACKET, _RDIVIDE, _RETURN, _RPAR, _SEMI,
                _SHORT_AND, _SHORT_OR, _SOL, _STRING, _SWITCH, _TILDE,
                _TIMES, _TRY, _UMINUS, _UNOT, _UPLUS, _WHILE, _WHITE,
                _anon_handle, _assignment, _bare_cell, _bare_array,
                _block_c_end, _block_c_start, _block_comment, _call_args,
                _case_stmt, _catch_stmt, _cell_access, _cell_array,
                _cell_name, _cmd_args, _cmd_name, _cmd_stmt, _comma_subs,
                _comma_values, _comment, _cond_expr, _continuation,
                _control_stmt, _delimiter, _elseif_stmt, _expr, _for_stmt,
                _fun_access, _fun_def_stmt, _fun_handle, _funcall_or_array,
                _fun_arr_name, _fun_name, _fun_outputs, _fun_params,
                _fun_paramslist, _handle_name, _if_stmt, _lhs_array,
                _lhs_var, _line_c_start, _line_comment, _logical_op,
                _matlab_syntax, _array_access, _array_args, _array_name,
                _multi_values, _named_handle, _noncmd_start, _one_row,
                _operand, _other_assign, _otherwise_stmt, _paren_expr,
                _plusminus, _power, _row_sep, _rows, _shell_cmd,
                _shell_cmd_cmd, _simple_assign, _single_value, _space_subs,
                _space_values, _stmt, _struct_access, _struct_base,
                _struct_field, _switch_stmt, _timesdiv, _transp_what,
                _transpose, _transpose_op, _try_stmt, _uplusminusneg,
                _while_stmt, _otherwise_stmt, _else_stmt, _end_stmt,
                _continue_stmt, _break_stmt, _return_stmt ]


    def _object_name(self, obj):
        """Returns the name of a given object."""
        try:
            values = MatlabGrammar.__dict__.iteritems()  # Python 2
        except:
            values = MatlabGrammar.__dict__.items()      # Python 3
        for name, thing in values:
            if thing is obj:
                return name


    _init_grammar_names_done = []

    def _init_grammar_names(self):
        if 'done' not in self._init_grammar_names_done:
            for obj in self._to_name:
                obj.setName(self._object_name(obj))
            self._init_grammar_names_done.append('done')


    # The next variable and function are for printing low-level PyParsing
    # matches.  To use this, manually add objects to the list, like so:
    #   _to_print_raw = [_cell_access, _cell_array, _bare_cell, _expr]
    _to_print_raw = []

    def _init_print_raw(self):
        for obj in self._to_print_raw:
            obj.setDebug(True)


    # Printers for debugging the ParseResults-based intermediate structure.
    #
    # This was the first approach to parsing and printing debugging output,
    # before the introduction of the MatlabNode-based output representation.
    # The code in the section below has been replaced by the methods that
    # work on the MatlabNode-based representation.  Nevertheless, the code is
    # left here because it might be useful for some debugging situations.
    # .........................................................................

    def _format(self, thing):
        return '\n'.join([self._format_pr(pr) for pr in thing])


    def _format_pr(self, pr):
        if isinstance(pr, str):
            return pr
        if not nonempty_dict(pr):
            # It's an expression.
            return self._format_expression(pr)
        key = first_key(pr)
        # Construct the function name dynamically.
        func_name = '_format_' + '_'.join(key.split())
        if func_name in MatlabGrammar.__dict__:
            return MatlabGrammar.__dict__[func_name](self, pr)
        else:
            self._warn('Internal error: no formatter for ' + str(key))


    def _format_identifier(self, pr):
        if not self._verified_pr(pr, 'identifier'):
            return
        content = pr['identifier']
        return '{{identifier: "{}"}}'.format(content)


    def _format_number(self, pr):
        if not self._verified_pr(pr, 'number'):
            return
        content = pr['number']
        return '{{number: {}}}'.format(content)


    def _format_boolean(self, pr):
        if not self._verified_pr(pr, 'boolean'):
            return
        content = pr['boolean']
        return '{{boolean: {}}}'.format(content)


    def _format_unary_operator(self, pr):
        op = 'unary op'
        op_key = first_key(pr)
        text = '{{{}: {}}}'.format(op, pr[op_key])
        return text


    def _format_binary_operator(self, pr):
        op = 'binary op'
        op_key = first_key(pr)
        text = '{{{}: {}}}'.format(op, pr[op_key])
        return text


    def _format_colon_operator(self, pr):
        return '{colon}'


    def _format_string(self, pr):
        if not self._verified_pr(pr, 'string'):
            return
        content = pr['string']
        return '{{string: "{}"}}'.format(content)


    def _format_assignment(self, pr):
        if not self._verified_pr(pr, 'assignment'):
            return
        content = pr['assignment']
        lhs = content['lhs']
        rhs = content['rhs']
        return '{{assign: {} = {}}}'.format(self._format_pr(lhs),
                                            self._format_pr(rhs))


    def _format_array_or_function(self, pr):
        if not self._verified_pr(pr, 'array or function'):
            return
        content = pr['array or function']
        name = content['name']
        args = self._help_format_simple_list(content['argument list'])
        return '{{function/array: {} ( {} )}}'.format(self._format_pr(name), args)


    def _format_array(self, pr):
        if not self._verified_pr(pr, 'array'):
            return
        content = pr['array']
        # Two kinds of array situations: a bare array, and one where we
        # managed to determine it's an array access (and not the more
        # ambiguous function call or array access).  If we have a row list,
        # it's the former; if we have an subscript list, it's the latter.
        if 'row list' in content:
            rows = self._help_format_rowlist(content['row list'])
            return '{{array: [ {} ]}}'.format(rows)
        elif 'subscript list' in content:
            name = self._format_pr(content['name'])
            subscripts = self._help_format_subscripts(content['subscript list'])
            return '{{array {}: [ {} ]}}'.format(name, subscripts)
        else:
            # No row list or subscript list => empty array.
            return '{array: [] }'


    def _format_cell_array(self, pr):
        if not self._verified_pr(pr, 'cell array'):
            return
        content = pr['cell array']
        rows = self._help_format_rowlist(content['row list'])
        if 'name' in content:
            name = content['name']
            return '{{cell array: {} [ {} ]}}'.format(self._format_pr(name), rows)
        else:
            return '{{cell array: [ {} ]}}'.format(rows)


    def _format_function_definition(self, pr):
        if not self._verified_pr(pr, 'function definition'):
            return
        content = pr['function definition']
        name = self._format_pr(content['name'])
        if 'output list' in content:
            output = self._help_format_simple_list(content['output list'])
            if len(content['output list']) > 1:
                output = '[ ' + output + ' ]'
        else:
            output = 'none'
        if 'parameter list' in content:
            param = self._help_format_simple_list(content['parameter list'])
        else:
            param = 'none'
        return '{{function definition: {} parameters ( {} ) output {}}}' \
            .format(name, param, output)


    def _format_function_handle(self, pr):
        if not self._verified_pr(pr, 'function handle'):
            return
        content = pr['function handle']
        if 'name' in content:
            name = content['name']
            return '{{function @ handle: {}}}'.format(self._format_pr(name))
        else:
            # No name => anonymous function
            arg_list = self._help_format_simple_list(content['argument list'])
            body = self._format_pr(content['function definition'])
            return '{{anon @ handle: args ( {} ) body {} }}'.format(arg_list, body)


    def _format_struct(self, pr):
        if not self._verified_pr(pr, 'struct'):
            return
        content = pr['struct']
        base = self._format_pr(content['struct base'])
        field = self._format_pr(content['field'])
        return '{{struct: {}.{} }}'.format(base, field)


    def _format_colon(self, pr):
        if not self._verified_pr(pr, 'colon'):
            return
        return '{colon}'


    def _format_transpose(self, pr):
        if not self._verified_pr(pr, 'transpose'):
            return
        content = pr['transpose']
        operand = self._format_pr(content['operand'])
        return '{{transpose: {} operator {} }}'.format(operand, content['operator'])


    def _format_shell_command(self, pr):
        if not self._verified_pr(pr, 'shell command'):
            return
        content = pr['shell command']
        body = content['command'][0]
        return '{{shell command: {}}}'.format(body)


    def _format_command_statement(self, pr):
        if not self._verified_pr(pr, 'command statement'):
            return
        content = pr['command statement']
        name = self._format_pr(content['name'])
        args = content['arguments'][0]
        return '{{command: name {} args {}}}'.format(name, args)


    def _format_comment(self, pr):
        if not self._verified_pr(pr, 'comment'):
            return
        content = pr['comment']
        return '{{comment: {}}}'.format(content[0])


    def _format_control_statement(self, pr):
        if not self._verified_pr(pr, 'control statement'):
            return
        content = pr['control statement']
        return self._format_pr(content)


    def _format_while_statement(self, pr):
        if not self._verified_pr(pr, 'while statement'):
            return
        content = pr['while statement']
        cond = self._format_pr(content['condition'])
        return '{{while stmt: {}}}'.format(cond)


    def _format_if_statement(self, pr):
        if not self._verified_pr(pr, 'if statement'):
            return
        content = pr['if statement']
        cond = self._format_pr(content['condition'])
        return '{{if stmt: {}}}'.format(cond)


    def _format_elseif_statement(self, pr):
        if not self._verified_pr(pr, 'elseif statement'):
            return
        content = pr['elseif statement']
        cond = self._format_pr(content['condition'])
        return '{{elseif stmt: {}}}'.format(cond)


    def _format_else_statement(self, pr):
        if not self._verified_pr(pr, 'else statement'):
            return
        return '{else}'


    def _format_switch_statement(self, pr):
        if not self._verified_pr(pr, 'switch statement'):
            return
        content = pr['switch statement']
        expr = self._format_pr(content['expression'])
        return '{{switch stmt: {}}}'.format(expr)


    def _format_case_statement(self, pr):
        if not self._verified_pr(pr, 'case statement'):
            return
        content = pr['case statement']
        expr = self._format_pr(content['expression'])
        return '{{case: {}}}'.format(expr)


    def _format_otherwise_statement(self, pr):
        if not self._verified_pr(pr, 'otherwise statement'):
            return
        return '{otherwise}'


    def _format_for_statement(self, pr):
        if not self._verified_pr(pr, 'for statement'):
            return
        content = pr['for statement']
        var = self._format_pr(content['loop variable'])
        exp = self._format_pr(content['expression'])
        return '{{for stmt: var {} in {}}}'.format(var, exp)


    def _format_try_statement(self, pr):
        if not self._verified_pr(pr, 'try statement'):
            return
        return '{try}'


    def _format_catch_statement(self, pr):
        if not self._verified_pr(pr, 'catch statement'):
            return
        content = pr['catch statement']
        var = self._format_pr(content['catch variable'])
        return '{{catch: var {}}}'.format(var)


    def _format_continue_statement(self, pr):
        if not self._verified_pr(pr, 'continue statement'):
            return
        return '{continue}'


    def _format_break_statement(self, pr):
        if not self._verified_pr(pr, 'break statement'):
            return
        return '{break}'


    def _format_return_statement(self, pr):
        if not self._verified_pr(pr, 'return statement'):
            return
        return '{return}'


    def _format_end_statement(self, pr):
        if not self._verified_pr(pr, 'end statement'):
            return
        return '{end}'


    def _format_tilde(self, pr):
        if not self._verified_pr(pr, 'tilde'):
            return
        return '{tilde}'


    def _format_expression(self, thing):
        return '( ' + ' '.join([self._format_pr(pr) for pr in thing]) + ' )'


    def _help_format_simple_list(self, pr):
        return ', '.join([self._format_pr(thing) for thing in pr])


    def _help_format_subscripts(self, subscripts):
        return ', '.join([self._format_pr(thing) for thing in subscripts])


    def _help_format_rowlist(self, arglist):
        last = len(arglist) - 1
        i = 1
        text = ''
        for row in arglist:
            if 'subscript list' not in row:
                self._warn('did not find "subscript list" key in ParseResults')
                return 'ERROR'
            subscripts = row['subscript list']
            text += '{{row {}: {}}}'.format(i, self._help_format_subscripts(subscripts))
            if i <= last:
                text += '; '
            i += 1
        return text


    def _warn(self, *args):
        print('WARNING: {}'.format(' '.join(args)))


    def _verified_pr(self, pr, type):
        if len(pr) > 1:
            self._warn('expected 1 ParseResults, but got {}'.format(len(pr)))
            return False
        if type not in pr:
            self._warn('ParseResults not of type {}'.format(type))
            return False
        return True


    # Instance initialization.
    # .........................................................................

    def __init__(self):
        self._init_grammar_names()
        # self._init_parse_actions()
        self._init_print_raw()
        self._reset()


    def _reset(self):
        self._context = None
        self._push_context(MatlabContext('(outermost context)'))


    # External interfaces.
    # .........................................................................

    def parse_string(self, input, print_debug=False, fail_soft=False):
        '''Parses MATLAB input and returns an a MatlabContext object.

        :param print_debug: if True, print debug output as each line is parsed.
        :param fail_soft: if True, don't raise an exception if parsing fails.

        '''
        self._reset()
        try:
            pr = self._matlab_syntax.parseString(input, parseAll=True)
            top_context = self._generate_nodes_and_contexts(pr)
            if print_debug:
                self.print_parse_results(top_context)
            return top_context
        except ParseException as err:
            msg = "Error: {0}".format(err)
            if fail_soft:
                print(msg)
                return None
            else:
                raise err


    def parse_file(self, path, print_debug=False, fail_soft=False):
        '''Parses the MATLAB contained in `file` and returns a MatlabContext.
        object This is essentially identical to MatlabGrammar.parse_string()
        but does the work of opening and closing the `file`.
        '''
        self._reset()
        try:
            file = open(path, 'r')
            contents = file.read()
            pr = self._matlab_syntax.parseString(contents, parseAll=True)
            top_context = self._generate_nodes_and_contexts(pr)
            top_context.file = path
            file.close()
            if print_debug:
                self.print_parse_results(top_context)
            return top_context
        except Exception as err:
            msg = "Error: {0}".format(err)
            if fail_soft:
                print(msg)
                return None
            else:
                raise err


    def print_parse_results(self, results, print_raw=False):
        '''Prints a representation of the parsed output given in `results`.
        This is intended for debugging purposes.  If `print_raw` is True,
        prints the underlying Python objects of the representation.  The
        objects in the output are valid Python objects, and in theory could
        be used to recreate the input or or less exactly.

        If `print_raw` is False (the default), prints the output in a
        slightly more human-readable form.
        '''
        if not isinstance(results, MatlabContext):
            raise ValueError("Expected a MatlabContext object")
        if print_raw:
            print('[')
            for node in results.nodes:
                print(repr(node))
            print(']')
        else:
            for node in results.nodes:
                print(node)


    @staticmethod
    def make_key(thing):
        '''Turns a parsed object like an array into a canonical text-string
        form, for use as a key in dictionaries such as MatlabContext.assignments.
        '''
        def row_to_string(row):
            list = [MatlabGrammar.make_key(item) for item in row]
            return ','.join(list)

        if isinstance(thing, str):
            return thing
        elif isinstance(thing, Primitive):
            return str(thing.value)
        elif isinstance(thing, Identifier):
            return str(thing.name)
        elif isinstance(thing, FunCall) or isinstance(thing, ArrayOrFunCall):
            base = MatlabGrammar.make_key(thing.name)
            return base + '(' + row_to_string(thing.args) + ')'
        elif isinstance(thing, ArrayRef):
            base = MatlabGrammar.make_key(thing.name)
            if thing.is_cell:
                return base + '{' + row_to_string(thing.args) + '}'
            else:
                return base + '(' + row_to_string(thing.args) + ')'
        elif isinstance(thing, StructRef):
            base = MatlabGrammar.make_key(thing.name)
            return base + '.' + MatlabGrammar.make_key(thing.field)
        elif isinstance(thing, Array):
            rowlist = [row_to_string(row) for row in thing.rows]
            return '[' + ';'.join(rowlist) + ']'
        elif isinstance(thing, Transpose):
            # Need test this special case of Operator before other Operators.
            # Note: Can't be called as LHS of an assignment, but we use make_key
            # for make_formula, and therefore need to do something sensible.
            return MatlabGrammar.make_key(thing.operand) + str(thing.operator)
        elif isinstance(thing, Operator):
            return str(thing.op)
        elif isinstance(thing, FunHandle):
            return str(thing)
        elif isinstance(thing, AnonFun):
            arg_list = row_to_string(thing.args)
            body = MatlabGrammar.make_key(thing.body)
            return '@(' + arg_list + ')' + body
        elif isinstance(thing, Comment) or isinstance(thing, FunDef) \
             or isinstance(thing, Command):
            # No reason for make_key called for these things, but must catch
            # random mayhem before falling through to the final case.
            return None
        elif isinstance(thing, Expression):
            the_list = [MatlabGrammar.make_formula(term, False) for term in thing.content]
            return '(' + ''.join(the_list) + ')'
        elif isinstance(thing, list):
            the_list = [MatlabGrammar.make_formula(term, False) for term in thing]
            return '(' + ''.join(the_list) + ')'
        else:
            # Something must be wrong if we get here.  Unclear what to do.
            return None


    @staticmethod
    def make_formula(thing, spaces=True, parens=True, atrans=None):
        '''Converted a mathematical expression into libSBML-style string form.
        The default behavior is to put spaces between terms and operators; if
        the optional flag 'spaces' is False, then no spaces are introduced.
        The default between is also to surround the expression with
        parentheses but if the optional flag 'parens' is False, the outermost
        parentheses (but not other parentheses) are omitted.  Finally, if
        given a function for parameter 'atrans' (default: none), it will
        call that function when it encounters array references.  The function
        will be given one argument, the array object, and should return a
        text string corresponding to the value to be used in place of the
        array.  If no 'atrans' is given, the default behavior is to render
        arrays like they would appear in Matlab text: e.g., "foo(2,3)".
        '''
        def make_list(thing):
            list = [MatlabGrammar.make_formula(term, spaces, parens, atrans)
                    for term in thing]
            sep = ''
            if spaces:
                sep = ' '
            if parens:
                return '(' + sep.join(list) + ')'
            else:
                return sep.join(list)

        if isinstance(thing, str):
            return thing
        elif isinstance(thing, Primitive):
            return str(thing.value)
        elif isinstance(thing, Identifier):
            return str(thing.name)
        elif isinstance(thing, Transpose):
            # Need test this special case of Operator before other Operators.
            # Note: Can't be called as LHS of an assignment, but we use make_key
            # for make_formula, and therefore need to do something sensible.
            return MatlabGrammar.make_key(thing)
        elif isinstance(thing, Operator):
            return str(thing.op)
        elif isinstance(thing, ArrayRef) or isinstance(thing, ArrayOrFunCall):
            if atrans:
                return atrans(thing)
            else:
                return MatlabGrammar.make_key(thing)
        elif isinstance(thing, StructRef) \
             or isinstance(thing, FunCall) \
             or isinstance(thing, AnonFun) \
             or isinstance(thing, FunHandle) \
             or isinstance(thing, Array):
            return MatlabGrammar.make_key(thing)
        elif isinstance(thing, Expression):
            return make_list(thing.content)
        elif isinstance(thing, list):
            return make_list(thing)
        elif 'comment' in thing:
            return ''
        else:
            # The remaining cases are things like command statements.  Those
            # shouldn't end up being called for make_formula.  Rather than
            # raise an error, though, this just returns None and lets the
            # caller deal with the problem.
            return None


# Notes about dealing with the intermediate PyParsing-based representation.
# .............................................................................
#
# First thing to know: the ParseResults objects generated by this
# PyParsing-based parser are heavily nested and annotated.  This is annoying
# to traverse by hand, and even more annoying to print (you will a *lot* of
# output for even simple things).  More on that below.
#
# The second thing to note is that the grammar is designed to attach
# PyParsing "result names" to matched components.  What this means is that
# most ParseResults objects have a Python dictionary associated with them.
# You can use Python's keys() operator to inspect the dictionary keys on most
# objects.  The basic approach to using the parsing results thus becomes a
# matter of using keys() to find out the entry or entries on an object,
# accessing the dictionary entries using the keys, calling keys() on the
# result of *that*, and so on, recursively.
#
# Here's an example using a simple assignment.  Suppose a file contains this:
#
#     a = 1
#
# This will return one ParseResults object at the top level, but this object
# will in reality be a recursively-structured list of annotated ParseResults
# objects.  (This will hopefully become more clear as this example
# progresses.)  At the top level, there will be one object per line in the
# file.  To find out how many there are, you can look at the length of what
# was returned by pyparsing's parseString(...):
#
#     (Pdb) type(results)
#     <class 'pyparsing.ParseResults'>
#     (Pdb) len(results)
#     1
#
# In this example, there's only one line in the file, so the length of the
# ParseResults object is one.  Let's get the ParseResults object for that
# first line:
#
#     (Pdb) content = results[0]
#     (Pdb) content.keys()
#     ['assignment']
#
# This first line of the file was labeled as an 'assignment', which is
# MatlabGrammar's way of identifying (you guessed it) an assignment
# statement.  Now let's look inside of it:
#
#     (Pdb) content['assignment'].keys()
#     ['rhs', 'lhs']
#
# The object stored under the dictionary key 'assignment' has its own
# dictionary, and that dictionary has two entries: one under the key 'lhs'
# (for the left-hand side of the assignment) and another under the key 'rhs'
# (for the right-hand side).  You can access each of these individually:
#
#     (Pdb) content['assignment']['lhs'].keys()
#     ['identifier']
#
# This now says that the object keyed by 'lhs' in the content['assignment']
# ParseResults object has another dictionary, with a single item stored under
# the key 'identifier'.  'identifier' is one of the terminal entities in
# MatlabGrammar.  When you access its value, you will find it's not a
# ParseResults object, but a string:
#
#     (Pdb) content['assignment']['lhs']['identifier']
#     'a'
#
# And there it is: the name of the variable on the left-hand side of the
# assignment in the file.  If we repeat the process for the right-hand side
# of the assignment, we find the following:
#
#     (Pdb) content['assignment']['rhs'].keys()
#     ['number']
#     (Pdb) content['assignment']['rhs']['number']
#     '1'
#
# The key 'number' corresponds to another terminal entity in MatlabGrammar.
# Its value is (you guessed it again) a number.  Note that the values
# returned by MatlabGrammar are always strings, so even though it could in
# principle be returned in the form of a numerical data type, MatlabGrammar
# does not do that, because doing so might require data type conversions and
# such conversions might require decisions that are best left to the
# applications calling MatlabGrammar.  So instead, it always returns
# everything in finds in a MATLAB file as a text string.
#
# Now, let's examine what happens if we have something slightly more
# complicated in the file, such as the following:
#
#     a = [1 2; 3 4]
#
# This is an assignment to an array that has two rows, each of which have two
# items.  Applying the parser to this input will once again yield a
# ParseResults object that itself contains a list of ParseResults objects,
# where the list will only have length one.  If we access the object,
#
#     (Pdb) content = results[0]
#     (Pdb) content.keys()
#     ['assignment']
#
# we once again have an assignment, as expected.  Let's take a look at the
# right-hand side of this one:
#
#     (Pdb) content['assignment']['rhs'].keys()
#     ['array']
#
# This time, MatlabGrammar has helpfully identified the object on the
# right-hand side as an array.  In the MATLAB world, a "matrix" is a
# two-dimensional array, but the MatlabGrammar grammar is not able to
# determine the number of dimensions of an array object; consequently, all of
# the homogeneous array objects (vectors, matrices, and arrays) are labeled
# as simply 'array'.  (MATLAB cell arrays are labeled 'cell array'.)  Now
# let's traverse the structure it produced:
#
#     (Pdb) array = content['assignment']['rhs']['array']
#     (Pdb) array.keys()
#     ['row list']
#     (Pdb) array['row list'].keys()
#     []
#
# This time, the object has no keys.  The reason for this is the following:
# some objects stored under the dictionary keys are actually lists.  The name
# 'row list' is meant to suggest this possibility.  When a value created by
# MatlabGrammar is a list, the first thing to do is to find out its length:
#
#     (Pdb) len(array['row list'])
#     2
#
# What this means is that the array has two row entries stored in a list
# keyed by 'row list'.  Accessing them is simple:
#
#     (Pdb) row1 = array['row list'][0]
#     (Pdb) row2 = array['row list'][1]
#     (Pdb) row1.keys()
#     ['subscript list']
#     (Pdb) row2.keys()
#     ['subscript list']
#
# Both of them have lists of their own.  These work in the same way as the
# row lists: you first find out their length, and then index into them to get
# the values.
#
#     (Pdb) len(row1)
#     2
#     (Pdb) row1[0].keys()
#     ['number']
#     (Pdb) row1[0]['number']
#     '1'
#
# As expected, we are down to the terminal parts of the expression, and here
# we have indexed into the first element of the first row of the array.  All
# of the rest of the entries in the array are accessed in the same way.  For
# example,
#
#     (Pdb) row2[1].keys()
#     ['number']
#     (Pdb) row2[1]['number']
#     '4'
#
# As a final example, let's take a look at a mathematical expression.
# Suppose our file contains the following:
#
#     a = 1 + 2
#
# As usual, applying the parser to this input will once again yield a
# ParseResults object that itself contains a list of ParseResults objects,
# and once again the list will only have length 1 because there is only one
# line in the file.  If we access the first object,
#
#     (Pdb) content = results[0]
#     (Pdb) content.keys()
#     ['assignment']
#
# we once again have an assignment, as expected.  Let's take a look at the
# right-hand side of this one:
#
#     (Pdb) content['assignment']['rhs'].keys()
#     []
#
# This time, the right-hand side does not have a key.  This is the tip-off
# that the right-hand side is an expression: in MatlabGrammar, if there is no
# key on an object, it means that the object is an expression or a list.
# Expressions are lists: when you encounter them, it means the next step is
# to iterate over the elements.
#
#     (Pdb) len(content['assignment']['rhs'])
#     3
#     (Pdb) content['assignment']['rhs'][0].keys()
#     ['number']
#     (Pdb) content['assignment']['rhs'][1].keys()
#     ['binary operator']
#     (Pdb) content['assignment']['rhs'][2].keys()
#     ['number']
#
# In this simple expression, the elements inside the expression list are
# terminal objects, but in general, they could be anything, including more
# expressions.  The rule for traversing expressions is the same: inspect the
# keys of each object, do whatever is appropriate for kind of object it is,
# and if there are no keys, it's another expression, so traverse it
# recursively.
#
# And that summarizes the basic process for working with MatlabGrammar parse
# results.  The parser(...) returns a list of objects results for the lines
# in the file; each has a dictionary, which you inspect to figure out what
# kind of objects were extracted, and then you dig into the object's
# dictionaries recursively until you reach terminal entities.  Sometimes the
# values for dictionaries are lists, in which case you iterate over the
# values, applying the same principle.
