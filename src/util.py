# This file is part of Rubber and thus covered by the GPL
# (c) Emmanuel Beffara, 2002--2005
"""
This module contains utility functions and classes used by the main system and
by the modules for various tasks.
"""

import md5
import os, stat
from os.path import *
import imp
import time
import re, string
from string import whitespace

from rubber import _, msg

#-- Miscellaneous functions

def md5_file (fname):
	"""
	Compute the MD5 sum of a given file.
	"""
	m = md5.new()
	file = open(fname)
	for line in file.readlines():
		m.update(line)
	file.close()
	return m.digest()

def simplify_path (file):
	"""
	Simplify a file name, removing a leading "./" or the directory name if
	it is the current working directory.
	"""
	dir = dirname(file)
	if dir == os.curdir or dir == os.getcwd():
		return basename(file)
	else:
		return file


#-- Keyval parsing

re_keyval = re.compile("\
[ ,]*\
(?P<key>[^ \t\n{}=,]+)\
([ \n\t]*=[ \n\t]*\
(?P<val>({|[^{},]*)))?")

def parse_keyval (str):
	"""
	Parse a list of 'key=value' pairs, with the syntax used in LaTeX's
	standard 'keyval' package. The value returned is simply a dictionary that
	contains all definitions found in the string. For keys without a value,
	the dictionary associates the value None.
	"""
	dict = {}
	while 1:
		m = re_keyval.match(str)
		if not m:
			return dict
		d = m.groupdict()
		str = str[m.end():]
		if not d["val"]:
			dict[d["key"]] = None
		elif d["val"] == '{':
			val, str = match_brace(str)
			dict[d["key"]] = val
		else:
			dict[d["key"]] = string.strip(d["val"])

def match_brace (str):
	"""
	Split the string at the first closing brace such that the extracted prefix
	is balanced with respect to braces. The return value is a pair. If the
	adequate closing brace is found, the pair contains the prefix before the
	brace and the suffix after the brace (not containing the brace). If no
	adequate brace is found, return the whole string as prefix and an empty
	string as suffix.
	"""
	level = 0
	for pos in range(0, len(str)):
		if str[pos] == '{':
			level = level + 1
		elif str[pos] == '}':
			level = level - 1
			if level == -1:
				return (str[:pos], str[pos+1:])
	return (str, "")


#-- Brace counting

def count_braces (str):
	"""
	Count the number of opening and closing braces in a string and return the
	difference, i.e. the number of levels open at the end.
	"""
	level = 0
	for pos in range(0, len(str)):
		if str[pos] == '{':
			level = level + 1
		elif str[pos] == '}':
			level = level - 1
	return level


#-- Extra argument parsing

def get_next_arg (dict):
	"""
	Assumes `dict' is a dictionary passed as argument to a macro hook, and
	extracts an argument from the current line, i.e. a balanced text in braces
	possibly preceded by spaces. Returns None if no argument is found.
	"""
	line = dict["line"].lstrip()
	if len(line) == 0 or line[0] != "{":
		return None
	arg, next = match_brace(line[1:])
	if next == "":
		return None
	dict["line"] = next
	return arg


#-- Checking for program availability

checked_progs = {}

def prog_available (prog):
	"""
	Test whether the specified program is available in the current path, and
	return its actual path if it is found, or None.
	"""
	if checked_progs.has_key(prog):
		return checked_progs[prog]
	for path in os.getenv("PATH").split(":"):
		file = os.path.join(path, prog)
		if os.path.exists(file):
			st = os.stat(file)
			if stat.S_ISREG(st.st_mode) and st.st_mode & 0111:
				checked_progs[prog] = file
				return file
	checked_progs[prog] = None
	return None


#-- Plugin management

class Plugins (object):
	"""
	This class gathers operations related to the management of external Python
	modules. Modules are requested through the `register' method, and
	they are searched for first in the current directory, then in the
	(possibly) specified Python package (using Python's path).
	"""
	def __init__ (self, path = None):
		"""
		Initialize the module set, possibly setting a path name in which
		modules will be searched for.
		"""
		self.modules = {}
		self.path = path

	def __getitem__ (self, name):
		"""
		Return the module object of the given name.
		"""
		return self.modules[name]

	def register (self, name):
		"""
		Attempt to register a module with the specified name. If an
		appropriate module is found, load it and store it in the object's
		dictionary. Return 0 if no module was found, 1 if a module was found
		and loaded, and 2 if the module was found but already loaded.
		"""
		if self.modules.has_key(name):
			return 2
		try:
			file, path, descr = imp.find_module(name, [""])
		except ImportError:
			if not self.path:
				return 0
			try:
				file, path, descr = imp.find_module(name, self.path)
			except ImportError:
				return 0
		module = imp.load_module(name, file, path, descr)
		file.close()
		self.modules[name] = module
		return 1

	def clear(self):
		"""
		Empty the module table, unregistering every module registered. No
		modules are unloaded, however, but this has no other effect than
		speeding the registration if the modules are loaded again.
		"""
		self.modules.clear()


#-- Automatic source conversion

class Converter (object):
	"""
	This class represents a set of translation rules that may be used to
	produce input files. Objects contain a table of rewriting rules to deduce
	potential source names from the target's name, and each rule has a give
	weight that indicates how expensive the translation is.

	Each rule associated to a given plugin name. Plugins are expected to
	contain a method 'convert' that take as argument the source (an existing
	file), the target, and the environment, and that returns a dependency node
	or None if the rule is not applicable.
	"""
	def __init__ (self, rules, plugins):
		"""
		Initialize the converter with a given set of rules. This set is a
		dictionary that associates regular expressions (to match the target
		names against) with dictionaries. Each of these dictionaries
		associates templates (that depend on the regular expression) for the
		source name with lists of couples (weight, plugin name). The third
		argument is the plugin set to use.
		See graphics.__init__ for an example.
		"""
		self.rules = {}
		for key, val in rules.items():
			self.rules[re.compile(key)] = val
		self.plugins = plugins

	def add_rule (self, target, source, weight, module):
		"""
		Define a new conversion rule. The arguments are, respectively, the
		expression to match the target against, the source name deduced from
		it, the weight of the rule and the module to use when a source is
		found. If another rule exists for the same translation, the new one
		has priority over it.
		"""
		e = re.compile(target)
		if not self.rules.has_key(e):
			self.rules[e] = {}
		dict = self.rules[e]
		if dict.has_key(source):
			dict[source].insert(0, (weight, module))
		else:
			dict[source] = [(weight, module)]

	def may_produce (self, name):
		"""
		Return true if the given filename may be that of a file generated by
		this converter, i.e. if it matches one of the target regular
		expressions.
		"""
		for dest in self.rules.keys():
			if dest.match(name):
				return 1
		return 0

	def __call__ (self, target, env, safe=0, **args):
		"""
		Search for an applicable rule for the given target. If such a rule is
		found, return the dependency node for the target with the least weight
		according to this rule, otherwise return None. If the optional
		argument 'safe' is true, then a dependency node is returned only if
		the source file may not be generated by the same converter. Other
		keyword arguments are passed to the converters.
		"""
		conv = []
		for dest, rules in self.rules.items():
			m = dest.match(target)
			if not m:
				continue
			for src, mods in rules.items():
				source = m.expand(src)
				if not exists(source):
					continue
				if safe and exists(target) and self.may_produce(source):
					return DependLeaf(env, target)
				for (weight, mod) in mods:
					if not self.plugins.register(mod):
						continue
					conv.append((weight, source, target, mod))

		conv.sort()
		for (weight, source, target, mod) in conv:
			dep = self.plugins[mod].convert(source, target, env, **args)
			if dep:
				return (weight, dep)

		return (None, None)


#-- Parsing commands

re_variable = re.compile("(?P<name>[a-zA-Z]*)")

def parse_line (line, dict):
	"""
	Decompose a string into a list of elements. The elements are separated by
	spaces, single and double quotes allow escaping of spaces (and quotes).
	Elements can contain variable references with the syntax '$VAR' (with only
	letters in the name) or '${VAR}'.

	If the argument 'dict' is defined, it is considered as a hash containing
	the values of the variables. If it is None, elements with variables are
	replaced by sequences of litteral strings or names, as follows:
		parse_line(" foo  bar${xy}quux toto  ")
			--> ["foo", ["'bar", "$xy", "'quux"], "toto"]
	"""
	elems = []
	i = 0
	size = len(line)
	while i < size:
		while i < size and line[i] in whitespace: i = i+1
		if i == size: break

		open = 0	# which quote is open
		arg = ""	# the current argument, so far
		if not dict: composed = None	# the current composed argument

		while i < size:
			c = line[i]

			# Open or close quotes.

			if c in '\'\"':
				if open == c: open = 0
				elif open: arg = arg + c
				else: open = c

			# '$' introduces a variable name, except within single quotes.

			elif c == '$' and open != "'":

				# Make the argument composed, if relevant.

				if not dict:
					if not composed: composed = []
					if arg != "": composed.append("'" + arg)
					arg = ""

				# Parse the variable name.

				if i+1 < size and line[i+1] == '{':
					end = line.find('}', i+2)
					if end < 0:
						name = line[i+2:]
						i = size
					else:
						name = line[i+2:end]
						i = end + 1
				else:
					m = re_variable.match(line, i+1)
					if m:
						name = m.group("name")
						i = m.end()
					else:
						name = ""
						i = i+1

				# Append the variable or its name.

				if dict:
					if dict.has_key(name):
						arg = arg + str(dict[name])
					# Send a warning for undefined variables ?
				else:
					composed.append("$" + name)
				continue

			# Handle spaces.

			elif c in whitespace:
				if open: arg = arg + c
				else: break
			else:
				arg = arg + c
			i = i+1

		# Append the new argument.

		if dict or not composed:
			elems.append(arg)
		else:
			if arg != "": composed.append("'" + arg)
			elems.append(composed)

	return elems

def make_line (template, dict):
	"""
	Instanciate a command template as returned by parse_line using a specific
	dictionary for variables.
	"""
	def one_string (arg):
		if arg.__class__ != list: return arg
		val = ""
		for elem in arg:
			if elem[0] == "'":
				val = val + elem[1:]
			else:
				if dict.has_key(elem[1:]):
					val = val + str(dict[elem[1:]])
		return val
	return map(one_string, template)
