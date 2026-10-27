// $Id$

/*
 Copyright (c) 2026, the booksim2 fabric contributors
 All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:

 Redistributions of source code must retain the above copyright notice, this
 list of conditions and the following disclaimer.
 Redistributions in binary form must reproduce the above copyright notice, this
 list of conditions and the following disclaimer in the documentation and/or
 other materials provided with the distribution.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
 ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
 ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

// Fabric: a network whose topology and routing come from files, for
// accelerator scale-up fabrics (tori, dragonfly-like group networks,
// switched fabrics). See doc/fabric/formats.md for the file formats.
//
// Routers may be chips (with terminals) or switches (no terminals). Router r
// has fabric_terminals terminals unless the topology file overrides it; its
// terminals take router ports 0..T-1 and nodes are numbered contiguously in
// router order. Link ports are numbered from 1 in the files and sit after the
// terminal ports on the router.
//
// Routing functions (routing_function = ...):
//   src     fixed per-(source, destination) router paths with explicit VCs
//           (fabric_path_file)
//   dor     torus dimension-order routing with a dateline VC per ring;
//           link kind = dimension + 4 * dateline
//   updown  up*/down* over the table's escape ports, +1 VC after each
//           global (kind 2) hop
//   duato   adaptive over every minimal port on VCs 0..n-3; escape on the
//           up*/down* ports, VC n-2 outside the destination group, n-1 in it
//   lduato  adaptive VCs 0..n-L-1 over every minimal port; layered escape
//           with fabric_esc_layers = L, escape VC n-L+layer
//   ugal    lduato plus a source choice between the minimal path and a
//           Valiant path through a random router in a third group
//   esc     deterministic minimal routing with VC escalation for two-level
//           group networks: +1 after a global (kind 2) hop and on each
//           kind 1 -> kind 0 turn
// Groups are the connected components of the non-global links.

#ifndef _FABRIC_HPP_
#define _FABRIC_HPP_

#include <string>
#include <vector>

#include "network.hpp"
#include "routefunc.hpp"

class Fabric : public Network {
  std::string _topo_file;

  void _ComputeSize(const Configuration &config);
  void _BuildNet(const Configuration &config);

public:
  Fabric(const Configuration &config, const std::string &name);
  static void RegisterRoutingFunctions();
};

void esc_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject);
void updown_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                   bool inject);
void duato_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                  bool inject);
void lduato_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                   bool inject);
void src_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject);
void ugal_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject);
void dor_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject);

#endif
