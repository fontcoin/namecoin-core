#!/usr/bin/env python3
# Copyright (c) 2014-2019 Daniel Kraft
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

# General code for Namecoin tests.

from .test_framework import BitcoinTestFramework

from .blocktools import (
  add_witness_commitment,
  create_block,
  create_coinbase,
)
from .messages import CTransaction
from .util import (
  assert_equal,
  gather_inputs,
  hex_str_to_bytes,
  sync_blocks,
  sync_mempools,
)

from decimal import Decimal
import io


def buildMultiUpdateBlock (node, name, values):
  """
  Constructs a CBlock instance that updates the name in a series to all
  of the given values.  This is something that the mempool policy does not
  allow, while it is perfectly fine in the consensus if included in a block.

  With this function, other tests can evaluate how this edge case is handled.
  """

  nameValue = Decimal ("0.01")
  fee = Decimal ("0.01")

  tip = node.getbestblockhash ()
  height = node.getblockcount () + 1
  nTime = node.getblockheader (tip)["mediantime"] + 1
  block = create_block (int (tip, 16), create_coinbase (height), nTime)

  # While iterating, we keep track of the previous transaction that we build
  # on.  Initialise with the current data for the name from the blockchain
  # as well as some random currency inputs to pay fees.
  prevVal, prevIns = gather_inputs (node, len (values) * fee)
  prevOuts = None
  prevNameOut = node.name_show (name)

  for v in values:
    ins = prevIns
    ins.append (prevNameOut)

    addrName = node.getnewaddress ()
    addrChange = node.getnewaddress ()
    outs = [{addrName: nameValue}, {addrChange: prevVal - fee}]

    txHex = node.createrawtransaction (ins, outs)
    nameOp = {"op": "name_update", "name": name, "value": v}
    txHex = node.namerawtransaction (txHex, 0, nameOp)["hex"]

    signed = node.signrawtransactionwithwallet (txHex, prevOuts)
    assert_equal (signed["complete"], True)
    txHex = signed["hex"]

    tx = CTransaction ()
    tx.deserialize (io.BytesIO (hex_str_to_bytes (txHex)))
    block.vtx.append (tx)

    # Update the variables about the previous transaction for this one,
    # so that the next is chained on correctly.
    txid = node.decoderawtransaction (txHex)["txid"]
    prevNameOut = {"txid": txid, "vout": 0}
    prevVal -= fee
    prevIns = [{"txid": txid, "vout": 1}]

    data = node.decoderawtransaction (txHex)
    prevOuts = []
    for i in range (len (data["vout"])):
      prevOuts.append ({
        "txid": txid,
        "vout": i,
        "scriptPubKey": data["vout"][i]["scriptPubKey"]["hex"]
      })

  add_witness_commitment (block, 0)
  block.solve ()

  return block


class NameTestFramework (BitcoinTestFramework):

  def setup_name_test (self, args = [[]] * 4):
    self.num_nodes = len (args)
    self.extra_args = args
    self.node_groups = None

    # Enable mocktime based on the value for the cached blockchain from
    # test_framework.py.  This is needed to get us out of IBD.
    self.mocktime = 1388534400 + (201 * 10 * 60)

  def split_network (self):
    # Override this method to keep track of the node groups, so that we can
    # sync_with_mode correctly.
    super ().split_network ()
    self.node_groups = [self.nodes[:2], self.nodes[2:]]

  def join_network (self):
    super ().join_network ()
    self.node_groups = None

  def sync_with_mode (self, mode = 'both'):
    modes = {'both': {'blocks': True, 'mempool': True},
             'blocks': {'blocks': True, 'mempool': False},
             'mempool': {'blocks': False, 'mempool': True}}
    assert mode in modes

    node_groups = self.node_groups
    if not node_groups:
        node_groups = [self.nodes]

    if modes[mode]['blocks']:
        [sync_blocks(group) for group in node_groups]
    if modes[mode]['mempool']:
        [sync_mempools(group) for group in node_groups]

  def firstupdateName (self, ind, name, newData, value,
                       opt = None, allowActive = False):
    """
    Utility routine to perform a name_firstupdate command.  The rand
    and txid are taken from 'newData', as it is returned by name_new.
    """

    node = self.nodes[ind]

    if allowActive:
      if opt is None:
        opt = {}
      return node.name_firstupdate (name, newData[1], newData[0],
                                    value, opt, True)

    if opt is None:
      return node.name_firstupdate (name, newData[1], newData[0], value)
    return node.name_firstupdate (name, newData[1], newData[0], value, opt)

  def generate (self, ind, blocks, syncBefore = True):
    """
    Generate blocks and sync all nodes.
    """

    # Sync before to get the mempools up-to-date and sync afterwards
    # to ensure that all blocks have propagated.

    if syncBefore:
        self.sync_with_mode ('both')
    self.nodes[ind].generate (blocks)
    self.sync_with_mode ('blocks')

  def checkName (self, ind, name, value, expiresIn, expired):
    """
    Query a name with name_show and check that certain data fields
    match the expectations.  Returns the full JSON object.
    """

    data = self.nodes[ind].name_show (name)
    self.checkNameData (data, name, value, expiresIn, expired)

    return data

  def checkNameData (self, data, name, value, expiresIn, expired):
    """
    Check a name info object against expected data.
    """

    assert_equal (data['name'], name)
    assert_equal (data['value'], value)
    if (expiresIn is not None):
      assert_equal (data['expires_in'], expiresIn)
    assert isinstance (data['expired'], bool)
    assert_equal (data['expired'], expired)

  def checkNameHistory (self, ind, name, values):
    """
    Query for the name_history of 'name' and check that its historical
    values, in order of increasing height, are the ones in 'values'.
    """

    data = self.nodes[ind].name_history (name)

    valuesFound = []
    for e in data:
      assert_equal (e['name'], name)
      valuesFound.append (e['value'])

    assert_equal (valuesFound, values)

  def rawtxOutputIndex (self, ind, txhex, addr):
    """
    Returns the index of the tx output in the given raw transaction that
    is sent to the given address.

    This is useful for building raw transactions with namerawtransaction.
    """

    tx = self.nodes[ind].decoderawtransaction (txhex)
    for i, vout in enumerate (tx['vout']):
      if addr in vout['scriptPubKey']['addresses']:
        return i

    return None

  def atomicTrade (self, name, value, price, fee, nameFrom, nameTo):
    """
    Perform an atomic name trade, sending 'name' from the first to the
    second node (referenced by their index).  Also send 'price' (we assume
    that it is less than each unspent output in 'listunspent') the
    other way round.  Returned is the txid.
    """

    addrA = self.nodes[nameFrom].getnewaddress ()
    addrB = self.nodes[nameTo].getnewaddress ()
    addrChange = self.nodes[nameTo].getrawchangeaddress ()

    inputs = []

    unspents = self.nodes[nameTo].listunspent ()
    txin = None
    for u in unspents:
      if u['amount'] >= price + fee:
        txin = u
        break
    assert txin is not None
    change = txin['amount'] - price - fee
    inputs.append ({"txid": txin['txid'], "vout": txin['vout']})

    data = self.nodes[nameFrom].name_show (name)
    nameTxo = self.nodes[nameFrom].gettxout (data['txid'], data['vout'])
    nameAmount = nameTxo['value']

    inputs.append ({"txid": data['txid'], "vout": data['vout']})
    outputs = {addrA: price, addrChange: change, addrB: nameAmount}
    tx = self.nodes[nameFrom].createrawtransaction (inputs, outputs)

    nameInd = self.rawtxOutputIndex (nameFrom, tx, addrB)
    nameOp = {"op": "name_update", "name": name, "value": value}
    tx = self.nodes[nameFrom].namerawtransaction (tx, nameInd, nameOp)

    signed = self.nodes[nameFrom].signrawtransactionwithwallet (tx['hex'])
    assert not signed['complete']
    signed = self.nodes[nameTo].signrawtransactionwithwallet (signed['hex'])
    assert signed['complete']
    tx = signed['hex']
    
    return self.nodes[nameFrom].sendrawtransaction (tx)
