# -*- test-case-name: flocker.node.agents.test.test_blockdevice -*-

"""
This module implements the parts of a block-device based dataset
convergence agent that can be re-used against many different kinds of block
devices.
"""
from uuid import uuid4
from subprocess import check_output

from zope.interface import implementer, Interface

from characteristic import attributes
from pyrsistent import PRecord, field

from twisted.internet.defer import succeed
from twisted.python.filepath import FilePath

from .. import IDeployer, IStateChange, InParallel
from ...control import Node, NodeState, Manifestation, Dataset

class VolumeException(Exception):
    """
    """
    def __init__(self, blockdevice_id):
        if not isinstance(blockdevice_id, unicode):
            raise TypeError(
                'Unexpected blockdevice_id type. '
                'Expected unicode. '
                'Got {!r}.'.format(blockdevice_id)
            )
        Exception.__init__(self, blockdevice_id)
        self.blockdevice_id = blockdevice_id


class UnknownVolume(VolumeException):
    """
    """


class AlreadyAttachedVolume(VolumeException):
    """
    """


class UnattachedVolume(VolumeException):
    pass



@implementer(IStateChange)
class CreateBlockDeviceDataset(PRecord):
    """
    An operation to create a new dataset on a newly created volume with a newly
    initialized filesystem.
    """
    dataset = field()
    mountpoint = field(mandatory=True)

    def run(self, deployer):
        api = deployer.block_device_api
        volume = api.create_volume(
            self.dataset.maximum_size
        )
        volume = api.attach_volume(
            volume.blockdevice_id, deployer.hostname.encode('ascii')
        )
        device = api.get_device_path(volume.blockdevice_id).path
        self.mountpoint.makedirs()
        check_output(["mkfs", "-t", "ext4", device])
        check_output(["mount", device, self.mountpoint.path])

        return succeed(None)

class IBlockDeviceAPI(Interface):
    """
    """

    def create_volume(size):
        """
        """

    def attach_volume(blockdevice_id, host):
        """
        """

    def list_volumes():
        """
        """

    def get_device_path(blockdevice_id):
        """
        """


class BlockDeviceVolume(PRecord):
    """
    """
    blockdevice_id = field(type=unicode, mandatory=True)
    size = field(type=int, mandatory=True)
    # XXX: This should be hostname, for consistency.
    host = field(type=(bytes, type(None)), initial=None)


def device_for_path(file_path):
    device_path =  check_output(
        ["losetup", "--noheadings", "-O", "name", "--associated", file_path.path]
    ).strip()
    if device_path != b"":
        return FilePath(device_path.strip())
    return None


@implementer(IBlockDeviceAPI)
@attributes(['root_path'])
class LoopbackBlockDeviceAPI(object):
    """
    """
    _attached_directory_name = 'attached'
    _unattached_directory_name = 'unattached'

    @classmethod
    def from_path(cls, root_path):
        """
        """
        api = cls(root_path=FilePath(root_path))
        api._initialise_directories()
        return api

    def _initialise_directories(self):
        """
        """
        self._unattached_directory = self.root_path.child(
            self._unattached_directory_name)

        try:
            self._unattached_directory.makedirs()
        except OSError:
            pass

        self._attached_directory = self.root_path.child(
            self._attached_directory_name)

        try:
            self._attached_directory.makedirs()
        except OSError:
            pass


    def get_device_path(self, blockdevice_id):
        volume = self._get(blockdevice_id)

        if volume.host is not None:
            volume_path = self._attached_directory.descendant([volume.host, volume.blockdevice_id])
            device_path = device_for_path(volume_path)
            if device_path is None:
                check_output(["losetup", "--find", volume_path.path])

            return device_for_path(volume_path)

        raise UnattachedVolume(blockdevice_id)

    def create_volume(self, size):
        """
        * create a file of some size (maybe size is required parameter)
        * put it in the IaaS's "unattached" directory
        """
        volume = BlockDeviceVolume(
            blockdevice_id=unicode(uuid4()),
            size=size,
        )
        self._unattached_directory.child(
            volume.blockdevice_id.encode('ascii')
        ).setContent(b'\0' * volume.size)
        return volume

    def _get(self, blockdevice_id):
        for volume in self.list_volumes():
            if volume.blockdevice_id == blockdevice_id:
                return volume
        raise UnknownVolume(blockdevice_id)

    def attach_volume(self, blockdevice_id, host):
        """
        * move file into per-host (eg named after node ip) directory
        """
        volume = self._get(blockdevice_id)
        if volume.host is None:
            old_path = self._unattached_directory.child(blockdevice_id)
            host_directory = self._attached_directory.child(host)
            attached_volume = volume.set(host=host)
            host_directory.makedirs()
            # try:
            #     host_directory.makedirs()
            # except OSError:
            #     pass
            old_path.moveTo(host_directory.child(blockdevice_id))
            return attached_volume

        raise AlreadyAttachedVolume(blockdevice_id)

    def list_volumes(self):
        """
        * list all files in "unattached" directory and all per-host directories
        """
        volumes = []
        for child in self.root_path.child('unattached').children():
            volume = BlockDeviceVolume(
                blockdevice_id=child.basename().decode('ascii'),
                size=child.getsize(),
            )
            volumes.append(volume)

        for host_directory in self.root_path.child('attached').children():
            host_name = host_directory.basename().encode('ascii')
            for child in host_directory.children():

                volume = BlockDeviceVolume(
                    blockdevice_id=child.basename().decode('ascii'),
                    size=child.getsize(),
                    host=host_name,
                )
                volumes.append(volume)

        return volumes


def _manifestation_from_volume(volume):
    """
    """
    dataset = Dataset(dataset_id=volume.blockdevice_id)
    return Manifestation(dataset=dataset, primary=True)


@implementer(IDeployer)
@attributes(["hostname", "block_device_api"])
class BlockDeviceDeployer(object):
    """
    """
    _mountroot = FilePath(b"/flocker")

    def discover_local_state(self):
        """
        """
        volumes = self.block_device_api.list_volumes()

        state = NodeState(
            hostname=self.hostname,
            running=(),
            not_running=(),
            manifestations=[_manifestation_from_volume(v)
                            for v in volumes
                            if v.host == self.hostname],
        )
        return succeed(state)

    def calculate_necessary_state_changes(self, local_state,
                                          desired_configuration,
                                          current_cluster_state):
        """
        """
        potential_configs = list(
            node for node in desired_configuration.nodes
            if node.hostname == self.hostname
        )
        if len(potential_configs) == 0:
            this_node_config = Node(hostname=None)
        else:
            [this_node_config] = potential_configs
        configured = set(this_node_config.manifestations.values())
        to_create = configured.difference(local_state.manifestations)

        # TODO check for non-None size on dataset; cannot create block devices
        # of unspecified size.
        creates = list(
            CreateBlockDeviceDataset(
                dataset=manifestation.dataset,
                mountpoint=self._mountroot.child(
                    manifestation.dataset.dataset_id.encode("ascii")
                )
            )
            for manifestation
            in to_create
        )
        return InParallel(changes=creates)
