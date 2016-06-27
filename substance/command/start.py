from substance.monads import *
from substance.logs import *
from substance import (Command, Engine)
from tabulate import tabulate

class Start(Command):
  def getShellOptions(self, optparser):
    optparser.add_option("-e","--engine", dest="engine", help="Engine to run this command on", default=None)
    optparser.add_option("-r","--reset", dest="reset", help="Stop & remove all containers before starting", default=False, action="store_true")
    return optparser

  def getUsage(self):
    return "substance start [options] [CONTAINER...]"

  def getHelpTitle(self):
    return "Start containers in the current subenv"

  def main(self):
    return self.core.loadCurrentEngine(name=self.getOption('engine')) \
      .bind(Engine.loadConfigFile) \
      .bind(Engine.envLoadCurrent) \
      .bind(Engine.envStart, reset=self.getOption('reset'), containers=self.args) \
      .catch(self.exitError)