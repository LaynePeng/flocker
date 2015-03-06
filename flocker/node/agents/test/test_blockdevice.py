"""
Tests for ``flocker.node.agents.blockdevice``.
"""
from uuid import uuid4

from zope.interface.verify import verifyObject

from ..blockdevice import (
    BlockDeviceDeployer, LoopbackBlockDeviceAPI, IBlockDeviceAPI,
    BlockDeviceVolume, UnknownVolume, AlreadyAttachedVolume,
    CreateBlockDeviceDataset, UnattachedVolume
)

from ... import InParallel, IDeployer, IStateChange
from ....control import Dataset, Manifestation, Node, NodeState, Deployment

from twisted.python.filepath import FilePath
from twisted.trial.unittest import SynchronousTestCase


class BlockDeviceDeployerTests(SynchronousTestCase):
    """
    Tests for ``BlockDeviceDeployer``.
    """
    def test_interface(self):
        """
        ``BlockDeviceDeployer`` instances provide ``IDeployer``.
        """
        api = LoopbackBlockDeviceAPI.from_path(self.mktemp())
        self.assertTrue(
            verifyObject(
                IDeployer,
                BlockDeviceDeployer(
                    hostname=b'192.0.2.123',
                    block_device_api=api
                )
            )
        )


class BlockDeviceDeployerDiscoverLocalStateTests(SynchronousTestCase):
    """
    Tests for ``BlockDeviceDeployer.discover_local_state``.
    """
    def setUp(self):
        self.expected_hostname = b'192.0.2.123'
        self.api = LoopbackBlockDeviceAPI.from_path(self.mktemp())
        self.deployer = BlockDeviceDeployer(
            hostname=self.expected_hostname,
            block_device_api=self.api
        )

    def assertDiscoveredState(self, deployer, expected_manifestations):
        """
        Assert that the manifestations on the state object returned by
        ``deployer.discover_local_state`` equals the given list of
        manifestations.

        :param IDeployer deployer: The object to use to discover the state.
        :param list expected_manifestations: The ``Manifestation``\ s expected
            to be discovered.

        :raise: A test failure exception if the manifestations are not what is
            expected.
        """
        discovering = deployer.discover_local_state()
        state = self.successResultOf(discovering)
        self.assertEqual(
            NodeState(
                hostname=deployer.hostname,
                running=(),
                not_running=(),
                manifestations=expected_manifestations,
            ),
            state
        )

    def test_no_devices(self):
        """
        ``BlockDeviceDeployer.discover_local_state`` returns a ``NodeState``
        with empty ``manifestations`` if the ``api`` reports no locally
        attached volumes.
        """
        self.assertDiscoveredState(self.deployer, [])

    def test_one_device(self):
        """
        ``BlockDeviceDeployer.discover_local_state`` returns a ``NodeState``
        with one ``manifestations`` if the ``api`` reports one locally
        attached volumes.
        """
        new_volume = self.api.create_volume(size=1234)
        attached_volume = self.api.attach_volume(
            new_volume.blockdevice_id, self.expected_hostname
        )
        expected_dataset = Dataset(dataset_id=attached_volume.blockdevice_id)
        expected_manifestation = Manifestation(dataset=expected_dataset, primary=True)
        self.assertDiscoveredState(self.deployer, [expected_manifestation])

    def test_only_remote_device(self):
        """
        ``BlockDeviceDeployer.discover_local_state`` does not consider remotely
        attached volumes.
        """
        new_volume = self.api.create_volume(size=1234)
        self.api.attach_volume(new_volume.blockdevice_id, b'some.other.host')
        self.assertDiscoveredState(self.deployer, [])

    def test_only_unattached_devices(self):
        """
        ``BlockDeviceDeployer.discover_local_state`` does not consider
        unattached volumes.
        """
        self.api.create_volume(size=1234)
        self.assertDiscoveredState(self.deployer, [])


class BlockDeviceDeployerCalculateNecessaryStateChangesTests(SynchronousTestCase):
    """
    Tests for ``BlockDeviceDeployer.calculate_necessary_state_changes``.
    """
    def test_no_devices_no_local_datasets(self):
        """
        If no devices exist and no datasets are part of the configuration for the
        deployer's node, no state changes are calculated.
        """
        dataset_id = unicode(uuid4())
        manifestation = Manifestation(
            dataset=Dataset(dataset_id=dataset_id), primary=True
        )
        node = b"192.0.2.1"
        other_node = b"192.0.2.2"
        configuration = Deployment(
            nodes=frozenset({
                Node(
                    hostname=other_node,
                    manifestations={dataset_id: manifestation},
                )
            })
        )
        state = Deployment(nodes=frozenset())
        api = LoopbackBlockDeviceAPI.from_path(self.mktemp())
        deployer = BlockDeviceDeployer(
            hostname=node,
            block_device_api=api,
        )
        changes = deployer.calculate_necessary_state_changes(
            local_state=NodeState(hostname=node),
            desired_configuration=configuration,
            current_cluster_state=state,
        )
        self.assertEqual(InParallel(changes=[]), changes)

    def test_no_devices_one_dataset(self):
        """
        If no devices exist but a dataset is part of the configuration for the
        deployer's node, a ``CreateBlockDeviceDataset`` change is calculated.
        """
        dataset_id = unicode(uuid4())
        dataset = Dataset(dataset_id=dataset_id)
        manifestation = Manifestation(
            dataset=dataset, primary=True
        )
        node = b"192.0.2.1"
        configuration = Deployment(
            nodes=frozenset({
                Node(
                    hostname=node,
                    manifestations={dataset_id: manifestation},
                )
            })
        )
        state = Deployment(nodes=frozenset())
        api = LoopbackBlockDeviceAPI.from_path(self.mktemp())
        deployer = BlockDeviceDeployer(
            hostname=node,
            block_device_api=api,
        )
        changes = deployer.calculate_necessary_state_changes(
            local_state=NodeState(hostname=node),
            desired_configuration=configuration,
            current_cluster_state=state,
        )
        mountpoint = deployer._mountroot.child(dataset_id.encode("ascii"))
        self.assertEqual(
            InParallel(
                changes=[
                    CreateBlockDeviceDataset(
                        dataset=dataset, mountpoint=mountpoint
                    )
                ]),
            changes
        )


class IBlockDeviceAPITestsMixin(object):
    """
    """
    def test_interface(self):
        """
        ``api`` instances provide ``IBlockDeviceAPI``.
        """
        self.assertTrue(
            verifyObject(IBlockDeviceAPI, self.api)
        )

    def test_list_volume_empty(self):
        """
        ``list_volumes`` returns an empty ``list`` if no block devices have
        been created.
        """
        self.assertEqual([], self.api.list_volumes())

    def test_created_is_listed(self):
        """
        ``create_volume`` returns a ``BlockVolume`` that is returned by
        ``list_volumes``.
        """
        new_volume = self.api.create_volume(size=1000)
        self.assertIn(new_volume, self.api.list_volumes())

    def test_attach_unknown_volume(self):
        """
        An attempt to attach an unknown ``BlockDeviceVolume`` raises
        ``UnknownVolume``.
        """
        self.assertRaises(
            UnknownVolume,
            self.api.attach_volume,
            blockdevice_id=unicode(uuid4()),
            host=b'192.0.2.123'
        )

    def test_attach_attached_volume(self):
        """
        An attempt to attach an already attached ``BlockDeviceVolume`` raises
        ``AlreadyAttachedVolume``.
        """
        host = b'192.0.2.123'
        new_volume = self.api.create_volume(size=1234)
        attached_volume = self.api.attach_volume(new_volume.blockdevice_id, host=host)

        self.assertRaises(
            AlreadyAttachedVolume,
            self.api.attach_volume,
            blockdevice_id=attached_volume.blockdevice_id,
            host=host
        )

    def test_attach_elsewhere_attached_volume(self):
        """
        An attempt to attach a ``BlockDeviceVolume`` already attached to
        another host raises ``AlreadyAttachedVolume``.
        """
        new_volume = self.api.create_volume(size=1234)
        attached_volume = self.api.attach_volume(new_volume.blockdevice_id, host=b'192.0.2.123')

        self.assertRaises(
            AlreadyAttachedVolume,
            self.api.attach_volume,
            blockdevice_id=attached_volume.blockdevice_id,
            host=b'192.0.2.124'
        )

    def test_attach_unattached_volume(self):
        """
        An unattached ``BlockDeviceVolume`` can be attached.
        """
        expected_host = b'192.0.2.123'
        new_volume = self.api.create_volume(size=1000)
        expected_volume = BlockDeviceVolume(
            blockdevice_id=new_volume.blockdevice_id,
            size=new_volume.size,
            host=expected_host,
        )
        attached_volume = self.api.attach_volume(
            blockdevice_id=new_volume.blockdevice_id,
            host=expected_host
        )
        self.assertEqual(expected_volume, attached_volume)

    def test_attached_volume_listed(self):
        """
        An attached ``BlockDeviceVolume`` is listed.
        """
        expected_host = b'192.0.2.123'
        new_volume = self.api.create_volume(size=1000)
        expected_volume = BlockDeviceVolume(
            blockdevice_id=new_volume.blockdevice_id,
            size=new_volume.size,
            host=expected_host,
        )
        self.api.attach_volume(
            blockdevice_id=new_volume.blockdevice_id,
            host=expected_host
        )
        self.assertEqual([expected_volume], self.api.list_volumes())

    # def test_get_attached_volume_device(self):
    #     1/0

    # def test_get_unattached_volume_device(self):
    #     1/0

    def test_get_device_path_unknown_volume(self):
        """
        ``get_device_path`` raises ``UnknownVolume`` if the supplied
        ``blockdevice_id`` has not been created.
        """
        unknown_blockdevice_id = unicode(uuid4())
        exception = self.assertRaises(
            UnknownVolume,
            self.api.get_device_path,
            unknown_blockdevice_id
        )
        self.assertEqual(unknown_blockdevice_id, exception.blockdevice_id)

    def test_get_device_path_unattached_volume(self):
        """
        ``get_device_path`` raises ``UnattachedVolume`` if the supplied
        ``blockdevice_id`` corresponds to an unattached volume.
        """
        new_volume = self.api.create_volume(size=1000)
        exception = self.assertRaises(
            UnattachedVolume,
            self.api.get_device_path,
            new_volume.blockdevice_id
        )
        self.assertEqual(new_volume.blockdevice_id, exception.blockdevice_id)

    def test_get_device_path_device(self):
        """
        ``get_device_path`` returns a ``FilePath`` to the device representing
        the attached volume.
        """
        new_volume = self.api.create_volume(size=1024 * 50)
        attached_volume = self.api.attach_volume(
            new_volume.blockdevice_id,
            b'192.0.2.123'
        )
        device_path = self.api.get_device_path(attached_volume.blockdevice_id)
        self.assertTrue(
            device_path.isBlockDevice(),
            u"Not a block device. Path: {!r}".format(device_path)
        )

    def test_get_device_path_device_exists(self):
        """
        ``get_device_path`` returns the same ``FilePath`` for the volume device
        when called multiple times.
        """
        new_volume = self.api.create_volume(size=1024 * 50)
        attached_volume = self.api.attach_volume(
            new_volume.blockdevice_id,
            b'192.0.2.123'
        )

        device_path1 = self.api.get_device_path(attached_volume.blockdevice_id)
        device_path2 = self.api.get_device_path(attached_volume.blockdevice_id)

        self.assertEqual(device_path1, device_path2)


def make_iblockdeviceapi_tests(blockdevice_api_factory):
    """
    """
    class Tests(IBlockDeviceAPITestsMixin, SynchronousTestCase):
        """
        """
        def setUp(self):
            self.api = blockdevice_api_factory(test_case=self)

    return Tests


def loopbackblockdeviceapi_for_test(test_case):
    """
    """
    root_path = test_case.mktemp()
    return LoopbackBlockDeviceAPI.from_path(root_path=root_path)


class LoopbackBlockDeviceAPITests(
        make_iblockdeviceapi_tests(blockdevice_api_factory=loopbackblockdeviceapi_for_test)
):
    """
    Interface adherence Tests for ``LoopbackBlockDeviceAPI``.
    """


class LoopbackBlockDeviceAPIImplementationTests(SynchronousTestCase):
    """
    Implementation specific tests for ``LoopbackBlockDeviceAPI``.
    """
    def assertDirectoryStructure(self, directory):
        """
        """
        attached_directory = directory.child(
            LoopbackBlockDeviceAPI._attached_directory_name
        )
        unattached_directory = directory.child(
            LoopbackBlockDeviceAPI._unattached_directory_name
        )

        LoopbackBlockDeviceAPI.from_path(directory.path)

        self.assertTrue(
            (True, True),
            (attached_directory.exists(), unattached_directory.exists())
        )

    def test_initialise_directories(self):
        """
        ``from_path`` creates a directory structure if it doesn't already
        exist.
        """
        directory = FilePath(self.mktemp()).child('loopback')
        self.assertDirectoryStructure(directory)

    def test_initialise_directories_attached_exists(self):
        """
        ``from_path`` uses existing attached directory if present.
        """
        directory = FilePath(self.mktemp())
        attached_directory = directory.child(
            LoopbackBlockDeviceAPI._attached_directory_name
        )
        attached_directory.makedirs()
        self.assertDirectoryStructure(directory)

    def test_initialise_directories_unattached_exists(self):
        """
        ``from_path`` uses existing unattached directory if present.
        """
        directory = FilePath(self.mktemp())
        unattached_directory = directory.child(
            LoopbackBlockDeviceAPI._unattached_directory_name
        )
        unattached_directory.makedirs()
        self.assertDirectoryStructure(directory)

    def test_multiple_volumes_attached_to_host(self):
        """
        """
        expected_host = b'192.0.2.123'
        api = loopbackblockdeviceapi_for_test(test_case=self)
        volume1 = api.create_volume(size=1234)
        volume2 = api.create_volume(size=1234)
        attached_volume1 = api.attach_volume(volume1.blockdevice_id, host=expected_host)
        attached_volume2 = api.attach_volume(volume2.blockdevice_id, host=expected_host)

        self.assertEqual(
            [attached_volume1, attached_volume2],
            api.list_volumes()
        )

    def test_list_unattached_volumes(self):
        """
        ``list_volumes`` returns a ``BlockVolume`` for each unattached volume
        file.
        """
        expected_size = 1234
        api = loopbackblockdeviceapi_for_test(test_case=self)
        blockdevice_volume = BlockDeviceVolume(
            blockdevice_id=unicode(uuid4()),
            size=expected_size,
        )
        (api
         .root_path.child('unattached')
         .child(blockdevice_volume.blockdevice_id)
         .setContent(b'x' * expected_size))
        self.assertEqual([blockdevice_volume], api.list_volumes())


    def test_list_attached_volumes(self):
        """
        ``list_volumes`` returns a ``BlockVolume`` for each attached volume
        file.
        """
        expected_size = 1234
        expected_host = b'192.0.2.123'
        api = loopbackblockdeviceapi_for_test(test_case=self)

        blockdevice_id = unicode(uuid4())

        host_dir = api.root_path.child('attached').child(expected_host)
        host_dir.makedirs()
        (host_dir
         .child(blockdevice_id)
         .setContent(b'x' * expected_size))

        blockdevice_volume = BlockDeviceVolume(
            blockdevice_id=blockdevice_id,
            size=expected_size,
            host=expected_host,
        )

        self.assertEqual([blockdevice_volume], api.list_volumes())


class CreateBlockDeviceDatasetTests(SynchronousTestCase):
    """
    Tests for ``CreateBlockDeviceDataset``.
    """
    def test_interface(self):
        """
        ``CreateBlockDeviceDataset`` provides ``IStateChange``.
        """
        self.assertTrue(
            verifyObject(
                IStateChange,
                CreateBlockDeviceDataset(
                    dataset=Dataset(dataset_id=unicode(uuid4())),
                    mountpoint=FilePath('.')
                )
            )
        )

    def test_run(self):
        """
        ``CreateBlockDeviceDataset.run`` uses the ``IDeployer``\ 's API object
        to create a new volume, attach it to the deployer's node, initialize
        the resulting block device with a filesystem, and mount the filesystem.
        """
        host = b"192.0.2.1"
        api = LoopbackBlockDeviceAPI.from_path(self.mktemp())
        deployer = BlockDeviceDeployer(hostname=host, block_device_api=api)
        deployer._mountroot = FilePath(self.mktemp())
        deployer._mountroot.makedirs()
        dataset_id = unicode(uuid4())
        mountpoint = deployer._mountroot.child(dataset_id.encode("ascii"))
        dataset = Dataset(dataset_id=dataset_id, maximum_size=1024 * 1024 * 10)
        change = CreateBlockDeviceDataset(dataset=dataset, mountpoint=mountpoint)
        change.run(deployer)

        [volume] = api.list_volumes()
        self.assertEqual(
            (dataset.maximum_size, host),
            (volume.size, volume.host)
        )

        mounts = list(get_mounts())

        self.assertIn(
            (api.get_device_path(volume.blockdevice_id).path,
             mountpoint.path,
             b"ext4"),
            mounts
        )


def get_mounts():
    """
    :returns: A generator 3-tuple(device_path, mountpoint, filesystem_type) for
        each currently mounted filesystem reported in ``/proc/self/mounts``.
    """
    with open("/proc/self/mounts") as mounts:
        for mount in mounts:
            device_path, mountpoint, filesystem_type = mount.split()[:3]
            yield device_path, mountpoint, filesystem_type
