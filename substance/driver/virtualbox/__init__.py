import os
import logging
from collections import OrderedDict
from substance.logs import *
from substance.monads import *
from substance.config import Config
from substance.driver import Driver
from substance.constants import (EngineStates)
from substance.exceptions import (FileDoesNotExist)

from vbox import (vboxManager)
import network
import machine
from exceptions import *

from netaddr import (IPAddress, IPNetwork)

class VirtualBoxDriver(Driver):
  '''
  Substance VirtualBox driver class. Interface to virtual box manager.
  '''

  def __init__(self, core):
    super(self.__class__, self).__init__(core)
    self.config = Config(os.path.join(self.core.getBasePath(), "virtualbox.yml"))

  # -- Configuration

  def getDefaultConfig(self):
    defaults = OrderedDict()
    defaults['network'] = "172.21.21.0/24"
    defaults['interface'] = None
    return defaults

  def makeDefaultConfig(self):
    logging.info("Generating default virtualbox config in %s" % self.config.getConfigFile())
    defaults = self.getDefaultConfig()
    for kkk, vvv in defaults.iteritems():
      self.config.set(kkk, vvv)
    return self.config.saveConfig()

  def assertConfig(self):
    return self.config.loadConfigFile() \
      .catchError(FileDoesNotExist, lambda err: self.makeDefaultConfig())
 
  #---- Networking setup
 
  def getNetworkInterface(self):
    return self.config.get('interface', None)

  def readNetworkConfig(self): 
    netrange = IPNetwork(self.config.get('network'))
    netconfig = {
      'gateway': netrange[1].format(),
      'netmask': netrange.netmask.format(),
      'lowerIP': netrange[2].format(),
      'upperIP': netrange[-1].format()
    }

    return OK(netconfig)

  def assertNetworking(self):
    interface = self.getNetworkInterface()
    netconfig = self.readNetworkConfig()

    if interface:
      hoif = network.readHostOnlyInterface(interface) \
        .catchError(VirtualBoxObjectNotFound, lambda err: OK(None))
      dhcp = network.readDHCP(interface) \
        .catchError(VirtualBoxObjectNotFound, lambda err: OK(None))
  
      return Try.sequence((netconfig, hoif, dhcp)) \
        .bind(self.assertNetworkConfiguration)
    else:
      return self.provisionNetworking(netconfig)
 
  def assertNetworkConfiguration(self, netparts):

    (netconfig, hoif, dhcp) = netparts

    if not hoif:
      return self.provisionNetworking(netconfig)
    elif hoif.ip.format() != netconfig['gateway']:
      logging.warn("VirtualBox interface \"%s\" is not properly configured. Creating a new host-only network." % hoif.name)
      return self.provisionNetworking(netconfig)
    elif dhcp is None:
      logging.warn("VirtualBox interface \"%s\" does not have DHCP enabled. Re-Establishing now." % hoif.name)
      return self.provisionDHCP(hoif.name, netconfig)

    return OK(hoif)
 
  def provisionNetworking(self, netconfig):
    logging.info("Provisioning VirtualBox networking for substance")
    ifm = network.addHostOnlyInterface() 
    if ifm.isFail():
      return ifm
    iface = ifm.getOK()

    return network.configureHostOnlyInterface(iface, ip=netconfig['gateway'], netmask=netconfig['netmask']) \
      .then(defer(self.provisionDHCP, interface=iface, netconfig=netconfig)) \
      .then(defer(self.saveInterface, iface=iface)) \
      .then(defer(network.readHostOnlyInterface, name=iface))

  def provisionDHCP(self, interface, netconfig):
    logging.info("Provisioning DHCP service for host only interface")
    network.removeDHCP(interface).catch(lambda x: OK(interface)) \
      .bind(defer(network.addDHCP, **netconfig))

  def saveInterface(self, iface):
    self.config.set('interface', iface)
    return self.config.saveConfig()

  #---- Machine API
  
  def importMachine(self, name, ovfFile, engineProfile=None):
    return self.assertConfig() \
      .then(defer(machine.inspectOVF, ovfFile)) \
      .bind(defer(machine.makeImportParams, name=name, engineProfile=engineProfile)) \
      .bind(defer(machine.importOVF, ovfFile=ovfFile, name=name))

  def startMachine(self, uuid):
    '''
    Start the machine by driver identifier.
    '''
    return self.assertConfig() \
      .then(self.assertNetworking) \
      .then(defer(machine.start, uuid))

  def suspendMachine(self, uuid):
    '''
    Suspend the machine.
    '''
    return machine.suspend(uuid)

  def haltMachine(self, uuid):
    '''
    Halt the machine.
    '''
    return machine.halt(uuid)
    
  def terminateMachine(self, uuid):
    '''
    Terminate the machine forcefully.
    '''
    return machine.terminate(uuid)

  def deleteMachine(self, uuid):
    '''
    Delete the machine by driver identifier.
    '''
    return machine.delete(uuid)

  def exportMachine(self, uuid):
    #XXX To be implemented
    pass
 
  def getMachines(self):
    '''
    Retrieve the list of machines and their driver identifiers.
    '''
    return machine.readMachines()

  # -- Parse results from Virtual Box

  def getMachineID(self, name):
    '''
    Retrieve the driver specific machine ID for a machine name.
    '''
    return machine.findMachineID(name)

  def exists(self, uuid):
    '''
    Check in the driver that the specified identifier exists.
    '''
    return machine.readMachineExists(uuid)

  def isRunning(self, uuid):
    if self.getMachineState(uuid) is EngineStates.RUNNING:
      return True

  def isStopped(self, uuid):
    if self.getMachineState(uuid) is not EngineStates.RUNNING:
      return True

  def isSuspended(self, uuid):
    if self.getMachineState(uuid) is EngineStates.SUSPENDED:
      return True

  def getMachineState(self, uuid):
    '''
    Retrieve the Substance machine state for this driver id
    '''
    return machine.readMachineState(uuid) \
      .bind(self.vboxStateToMachineState)

  def vboxStateToMachineState(self, vboxState):
    '''
    Resolve a vbox machine state to a substance engine state.
    '''
    mapping = {
      "poweroff": EngineStates.STOPPED,
      "saved": EngineStates.SUSPENDED,
      "aborted": EngineStates.STOPPED,
      "paused": EngineStates.STOPPED,
      "stuck": EngineStates.STOPPED,
      "restoring": EngineStates.STOPPED,
      "snapshotting": EngineStates.STOPPED,
      "setting up": EngineStates.STOPPED,
      "online snapshotting": EngineStates.STOPPED,
      "restoring snapshot": EngineStates.STOPPED,
      "deleting snapshot": EngineStates.STOPPED,
      "running": EngineStates.RUNNING,
      "starting": EngineStates.RUNNING,
      "stopping" : EngineStates.RUNNING,
      "saving": EngineStates.RUNNING,
      "live snapshotting": EngineStates.RUNNING,
      "unknown": EngineStates.UNKNOWN,
      "inaccessible": EngineStates.INEXISTENT,
      "inexistent": EngineStates.INEXISTENT
    }
    state = mapping.get(vboxState, EngineStates.UNKNOWN)
    ddebug("Machine state: %s : %s", vboxState, state)
    return OK(state)


