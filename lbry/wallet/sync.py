import asyncio
import logging
#from io import StringIO
#from functools import partial
#from operator import itemgetter
from collections import defaultdict
#from binascii import hexlify, unhexlify
from typing import List, Optional, DefaultDict, NamedTuple

#from lbry.crypto.hash import double_sha256, sha256

from lbry.tasks import TaskGroup
from lbry.blockchain.transaction import Transaction
#from lbry.blockchain.block import get_block_filter
from lbry.event import EventController
from lbry.service.base import Service, Sync

from .account import AddressManager


log = logging.getLogger(__name__)


class TransactionEvent(NamedTuple):
    address: str
    tx: Transaction


class AddressesGeneratedEvent(NamedTuple):
    address_manager: AddressManager
    addresses: List[str]


class TransactionCacheItem:
    __slots__ = '_tx', 'lock', 'has_tx', 'pending_verifications'

    def __init__(self, tx: Optional[Transaction] = None, lock: Optional[asyncio.Lock] = None):
        self.has_tx = asyncio.Event()
        self.lock = lock or asyncio.Lock()
        self._tx = self.tx = tx
        self.pending_verifications = 0

    @property
    def tx(self) -> Optional[Transaction]:
        return self._tx

    @tx.setter
    def tx(self, tx: Transaction):
        self._tx = tx
        if tx is not None:
            self.has_tx.set()


class SPVSync(Sync):

    def __init__(self, service: Service):
        super().__init__(service.ledger, service.db)

        self.accounts = []

        self._on_header_controller = EventController()
        self.on_header = self._on_header_controller.stream
        self.on_header.listen(
            lambda change: log.info(
                '%s: added %s header blocks',
                self.ledger.get_id(), change
            )
        )
        self._download_height = 0

        self._on_ready_controller = EventController()
        self.on_ready = self._on_ready_controller.stream

        #self._tx_cache = pylru.lrucache(100000)
        self._update_tasks = TaskGroup()
        self._other_tasks = TaskGroup()  # that we dont need to start
        self._header_processing_lock = asyncio.Lock()
        self._address_update_locks: DefaultDict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._known_addresses_out_of_sync = set()

    # async def advance(self):
    #     address_array = [
    #         bytearray(a['address'].encode())
    #         for a in await self.service.db.get_all_addresses()
    #     ]
    #     block_filters = await self.service.get_block_address_filters()
    #     for block_hash, block_filter in block_filters.items():
    #         bf = get_block_filter(block_filter)
    #         if bf.MatchAny(address_array):
    #             print(f'match: {block_hash} - {block_filter}')
    #             tx_filters = await self.service.get_transaction_address_filters(block_hash=block_hash)
    #             for txid, tx_filter in tx_filters.items():
    #                 tf = get_block_filter(tx_filter)
    #                 if tf.MatchAny(address_array):
    #                     print(f'  match: {txid} - {tx_filter}')
    #                     txs = await self.service.search_transactions([txid])
    #                     tx = Transaction(unhexlify(txs[txid]))
    #                     await self.service.db.insert_transaction(tx)
    #
    # async def get_local_status_and_history(self, address, history=None):
    #     if not history:
    #         address_details = await self.db.get_address(address=address)
    #         history = (address_details['history'] if address_details else '') or ''
    #     parts = history.split(':')[:-1]
    #     return (
    #         hexlify(sha256(history.encode())).decode() if history else None,
    #         list(zip(parts[0::2], map(int, parts[1::2])))
    #     )
    #
    # @staticmethod
    # def get_root_of_merkle_tree(branches, branch_positions, working_branch):
    #     for i, branch in enumerate(branches):
    #         other_branch = unhexlify(branch)[::-1]
    #         other_branch_on_left = bool((branch_positions >> i) & 1)
    #         if other_branch_on_left:
    #             combined = other_branch + working_branch
    #         else:
    #             combined = working_branch + other_branch
    #         working_branch = double_sha256(combined)
    #     return hexlify(working_branch[::-1])
    #
    async def start(self):
        fully_synced = self.on_ready.first
        #asyncio.create_task(self.network.start())
        #await self.network.on_connected.first
        #async with self._header_processing_lock:
        #    await self._update_tasks.add(self.initial_headers_sync())
        await fully_synced
    #
    # async def join_network(self, *_):
    #     log.info("Subscribing and updating accounts.")
    #     await self._update_tasks.add(self.subscribe_accounts())
    #     await self._update_tasks.done.wait()
    #     self._on_ready_controller.add(True)
    #
    async def stop(self):
        self._update_tasks.cancel()
        self._other_tasks.cancel()
        await self._update_tasks.done.wait()
        await self._other_tasks.done.wait()
    #
    # @property
    # def local_height_including_downloaded_height(self):
    #     return max(self.headers.height, self._download_height)
    #
    # async def initial_headers_sync(self):
    #     get_chunk = partial(self.network.retriable_call, self.network.get_headers, count=1000, b64=True)
    #     self.headers.chunk_getter = get_chunk
    #
    #     async def doit():
    #         for height in reversed(sorted(self.headers.known_missing_checkpointed_chunks)):
    #             async with self._header_processing_lock:
    #                 await self.headers.ensure_chunk_at(height)
    #     self._other_tasks.add(doit())
    #     await self.update_headers()
    #
    # async def update_headers(self, height=None, headers=None, subscription_update=False):
    #     rewound = 0
    #     while True:
    #
    #         if height is None or height > len(self.headers):
    #             # sometimes header subscription updates are for a header in the future
    #             # which can't be connected, so we do a normal header sync instead
    #             height = len(self.headers)
    #             headers = None
    #             subscription_update = False
    #
    #         if not headers:
    #             header_response = await self.network.retriable_call(self.network.get_headers, height, 2001)
    #             headers = header_response['hex']
    #
    #         if not headers:
    #             # Nothing to do, network thinks we're already at the latest height.
    #             return
    #
    #         added = await self.headers.connect(height, unhexlify(headers))
    #         if added > 0:
    #             height += added
    #             self._on_header_controller.add(
    #                 BlockHeightEvent(self.headers.height, added))
    #
    #             if rewound > 0:
    #                 # we started rewinding blocks and apparently found
    #                 # a new chain
    #                 rewound = 0
    #                 await self.db.rewind_blockchain(height)
    #
    #             if subscription_update:
    #                 # subscription updates are for latest header already
    #                 # so we don't need to check if there are newer / more
    #                 # on another loop of update_headers(), just return instead
    #                 return
    #
    #         elif added == 0:
    #             # we had headers to connect but none got connected, probably a reorganization
    #             height -= 1
    #             rewound += 1
    #             log.warning(
    #                 "Blockchain Reorganization: attempting rewind to height %s from starting height %s",
    #                 height, height+rewound
    #             )
    #
    #         else:
    #             raise IndexError(f"headers.connect() returned negative number ({added})")
    #
    #         if height < 0:
    #             raise IndexError(
    #                 "Blockchain reorganization rewound all the way back to genesis hash. "
    #                 "Something is very wrong. Maybe you are on the wrong blockchain?"
    #             )
    #
    #         if rewound >= 100:
    #             raise IndexError(
    #                 "Blockchain reorganization dropped {} headers. This is highly unusual. "
    #                 "Will not continue to attempt reorganizing. Please, delete the ledger "
    #                 "synchronization directory inside your wallet directory (folder: '{}') and "
    #                 "restart the program to synchronize from scratch."
    #                     .format(rewound, self.ledger.get_id())
    #             )
    #
    #         headers = None  # ready to download some more headers
    #
    #         # if we made it this far and this was a subscription_update
    #         # it means something went wrong and now we're doing a more
    #         # robust sync, turn off subscription update shortcut
    #         subscription_update = False
    #
    # async def receive_header(self, response):
    #     async with self._header_processing_lock:
    #         header = response[0]
    #         await self.update_headers(
    #             height=header['height'], headers=header['hex'], subscription_update=True
    #         )
    #
    # async def subscribe_accounts(self):
    #     if self.network.is_connected and self.accounts:
    #         log.info("Subscribe to %i accounts", len(self.accounts))
    #         await asyncio.wait([
    #             self.subscribe_account(a) for a in self.accounts
    #         ])
    #
    # async def subscribe_account(self, account: Account):
    #     for address_manager in account.address_managers.values():
    #         await self.subscribe_addresses(address_manager, await address_manager.get_addresses())
    #     await account.ensure_address_gap()
    #
    # async def unsubscribe_account(self, account: Account):
    #     for address in await account.get_addresses():
    #         await self.network.unsubscribe_address(address)
    #
    # async def subscribe_addresses(
    #       self, address_manager: AddressManager, addresses: List[str], batch_size: int = 1000):
    #     if self.network.is_connected and addresses:
    #         addresses_remaining = list(addresses)
    #         while addresses_remaining:
    #             batch = addresses_remaining[:batch_size]
    #             results = await self.network.subscribe_address(*batch)
    #             for address, remote_status in zip(batch, results):
    #                 self._update_tasks.add(self.update_history(address, remote_status, address_manager))
    #             addresses_remaining = addresses_remaining[batch_size:]
    #             log.info("subscribed to %i/%i addresses on %s:%i", len(addresses) - len(addresses_remaining),
    #                      len(addresses), *self.network.client.server_address_and_port)
    #         log.info(
    #             "finished subscribing to %i addresses on %s:%i", len(addresses),
    #             *self.network.client.server_address_and_port
    #         )
    #
    # def process_status_update(self, update):
    #     address, remote_status = update
    #     self._update_tasks.add(self.update_history(address, remote_status))
    #
    # async def update_history(self, address, remote_status, address_manager: AddressManager = None):
    #     async with self._address_update_locks[address]:
    #         self._known_addresses_out_of_sync.discard(address)
    #
    #         local_status, local_history = await self.get_local_status_and_history(address)
    #
    #         if local_status == remote_status:
    #             return True
    #
    #         remote_history = await self.network.retriable_call(self.network.get_history, address)
    #         remote_history = list(map(itemgetter('tx_hash', 'height'), remote_history))
    #         we_need = set(remote_history) - set(local_history)
    #         if not we_need:
    #             return True
    #
    #         cache_tasks: List[asyncio.Task[Transaction]] = []
    #         synced_history = StringIO()
    #         loop = asyncio.get_running_loop()
    #         for i, (txid, remote_height) in enumerate(remote_history):
    #             if i < len(local_history) and local_history[i] == (txid, remote_height) and not cache_tasks:
    #                 synced_history.write(f'{txid}:{remote_height}:')
    #             else:
    #                 check_local = (txid, remote_height) not in we_need
    #                 cache_tasks.append(loop.create_task(
    #                     self.cache_transaction(unhexlify(txid)[::-1], remote_height, check_local=check_local)
    #                 ))
    #
    #         synced_txs = []
    #         for task in cache_tasks:
    #             tx = await task
    #
    #             check_db_for_txos = []
    #             for txi in tx.inputs:
    #                 if txi.txo_ref.txo is not None:
    #                     continue
    #                 cache_item = self._tx_cache.get(txi.txo_ref.tx_ref.hash)
    #                 if cache_item is not None:
    #                     if cache_item.tx is None:
    #                         await cache_item.has_tx.wait()
    #                     assert cache_item.tx is not None
    #                     txi.txo_ref = cache_item.tx.outputs[txi.txo_ref.position].ref
    #                 else:
    #                     check_db_for_txos.append(txi.txo_ref.hash)
    #
    #             referenced_txos = {} if not check_db_for_txos else {
    #                 txo.id: txo for txo in await self.db.get_txos(
    #                     txo_hash__in=check_db_for_txos, order_by='txo.txo_hash', no_tx=True
    #                 )
    #             }
    #
    #             for txi in tx.inputs:
    #                 if txi.txo_ref.txo is not None:
    #                     continue
    #                 referenced_txo = referenced_txos.get(txi.txo_ref.id)
    #                 if referenced_txo is not None:
    #                     txi.txo_ref = referenced_txo.ref
    #
    #             synced_history.write(f'{tx.id}:{tx.height}:')
    #             synced_txs.append(tx)
    #
    #         await self.db.save_transaction_io_batch(
    #             synced_txs, address, self.ledger.address_to_hash160(address), synced_history.getvalue()
    #         )
    #         await asyncio.wait([
    #             self.ledger._on_transaction_controller.add(TransactionEvent(address, tx))
    #             for tx in synced_txs
    #         ])
    #
    #         if address_manager is None:
    #             address_manager = await self.get_address_manager_for_address(address)
    #
    #         if address_manager is not None:
    #             await address_manager.ensure_address_gap()
    #
    #         local_status, local_history = \
    #             await self.get_local_status_and_history(address, synced_history.getvalue())
    #         if local_status != remote_status:
    #             if local_history == remote_history:
    #                 return True
    #             log.warning(
    #                 "Wallet is out of sync after syncing. Remote: %s with %d items, local: %s with %d items",
    #                 remote_status, len(remote_history), local_status, len(local_history)
    #             )
    #             log.warning("local: %s", local_history)
    #             log.warning("remote: %s", remote_history)
    #             self._known_addresses_out_of_sync.add(address)
    #             return False
    #         else:
    #             return True
    #
    # async def cache_transaction(self, tx_hash, remote_height, check_local=True):
    #     cache_item = self._tx_cache.get(tx_hash)
    #     if cache_item is None:
    #         cache_item = self._tx_cache[tx_hash] = TransactionCacheItem()
    #     elif cache_item.tx is not None and \
    #             cache_item.tx.height >= remote_height and \
    #             (cache_item.tx.is_verified or remote_height < 1):
    #         return cache_item.tx  # cached tx is already up-to-date
    #
    #     try:
    #         cache_item.pending_verifications += 1
    #         return await self._update_cache_item(cache_item, tx_hash, remote_height, check_local)
    #     finally:
    #         cache_item.pending_verifications -= 1
    #
    # async def _update_cache_item(self, cache_item, tx_hash, remote_height, check_local=True):
    #
    #     async with cache_item.lock:
    #
    #         tx = cache_item.tx
    #
    #         if tx is None and check_local:
    #             # check local db
    #             tx = cache_item.tx = await self.db.get_transaction(tx_hash=tx_hash)
    #
    #         merkle = None
    #         if tx is None:
    #             # fetch from network
    #             _raw, merkle = await self.network.retriable_call(
    #                 self.network.get_transaction_and_merkle, tx_hash, remote_height
    #             )
    #             tx = Transaction(unhexlify(_raw), height=merkle.get('block_height'))
    #             cache_item.tx = tx  # make sure it's saved before caching it
    #         await self.maybe_verify_transaction(tx, remote_height, merkle)
    #         return tx
    #
    # async def maybe_verify_transaction(self, tx, remote_height, merkle=None):
    #     tx.height = remote_height
    #     cached = self._tx_cache.get(tx.hash)
    #     if not cached:
    #         # cache txs looked up by transaction_show too
    #         cached = TransactionCacheItem()
    #         cached.tx = tx
    #         self._tx_cache[tx.hash] = cached
    #     if 0 < remote_height < len(self.headers) and cached.pending_verifications <= 1:
    #         # can't be tx.pending_verifications == 1 because we have to handle the transaction_show case
    #         if not merkle:
    #             merkle = await self.network.retriable_call(self.network.get_merkle, tx.hash, remote_height)
    #         merkle_root = self.get_root_of_merkle_tree(merkle['merkle'], merkle['pos'], tx.hash)
    #         header = await self.headers.get(remote_height)
    #         tx.position = merkle['pos']
    #         tx.is_verified = merkle_root == header['merkle_root']
    #
    # async def get_address_manager_for_address(self, address) -> Optional[AddressManager]:
    #     details = await self.db.get_address(address=address)
    #     for account in self.accounts:
    #         if account.id == details['account']:
    #             return account.address_managers[details['chain']]
    #     return None