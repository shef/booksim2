// Message workloads (sim_type = messages, doc/fabric/messages.md): a fixed
// list of messages between endpoints, each sent once its dependencies have
// arrived (plus a delay), reporting completion times instead of rates.

#ifndef _MESSAGETRAFFICMANAGER_HPP_
#define _MESSAGETRAFFICMANAGER_HPP_

#include <iostream>
#include <queue>
#include <string>
#include <utility>
#include <vector>

#include "config_utils.hpp"
#include "trafficmanager.hpp"

class MessageTrafficManager : public TrafficManager {

  struct Message {
    int id, src, dst, tag;   // src, dst: endpoint indices
    long long bytes;
    int packets, last_size;  // packets of packet_size flits; last one's size
    int issued, retired;     // packets so far
    int deps_left;
    long long delay;         // cycles after the last dependency finishes
    long long ready, first_inject, finish;  // cycles, -1 until known
    std::vector<int> dependents;
    // stream=: (source message, packets of it granted so far); this message
    // may send a share of its packets equal to the least granted share.
    std::vector<std::pair<int, int> > streams;
    std::vector<std::pair<int, int> > stream_dependents;  // (message, slot)
    bool released, listed;  // after= deps done (+delay); in a ready list
  };

  std::vector<Message> _msgs;
  std::vector<std::string> _tags;

  // Endpoints: routers with terminals (fabric), else every node; then each
  // router's compute terminals as endpoint R + r, written cR in files.
  int _routers_n;
  std::vector<int> _ep_first, _ep_count;  // [endpoint] node range
  std::vector<int> _node_ep;              // [node] endpoint

  std::vector<std::vector<int> > _ready;  // [endpoint] ready messages
  std::vector<size_t> _rr;                // [endpoint] round-robin position
  std::priority_queue<std::pair<long long, int>,
                      std::vector<std::pair<long long, int> >,
                      std::greater<std::pair<long long, int> > > _pending;  // (ready, msg)

  // (time, message, stream slot): a streamed packet's grant comes due
  std::priority_queue<std::pair<long long, std::pair<int, int> >,
                      std::vector<std::pair<long long, std::pair<int, int> > >,
                      std::greater<std::pair<long long, std::pair<int, int> > > > _grants;

  std::vector<int> _next_msg, _next_dest, _next_size;  // [node] packet being generated
  std::vector<int> _pid_msg;                           // [packet id] message

  int _flit_bytes, _packet_size;
  double _cycle_ns;
  long long _stall_cycles;
  int _window;                   // message_window: packets in flight per endpoint (0 = no limit)
  std::vector<int> _in_flight;   // [endpoint] packets issued, tail not yet ejected
  std::string _out_file;
  int _finished;
  long long _last_retire;

  void _ReadEndpoints();
  int _ParseEndpoint(const std::string &tok) const;
  std::string _EndpointName(int ep) const;
  void _ReadMessages(const std::string &file);
  void _Release();
  int _Allowed(const Message &m) const;
  void _Requeue(int i);
  void _Report(std::ostream &os) const;
  void _ReportStall(std::ostream &os) const;

protected:
  virtual int _IssuePacket(int source, int cl);
  virtual int _PacketSize(int source, int cl);
  virtual int _PacketDestination(int source, int cl);
  virtual void _RetireFlit(Flit *f, int dest);
  virtual bool _SingleSim();

public:
  MessageTrafficManager(const Configuration &config, const std::vector<Network *> &net);
};

#endif
