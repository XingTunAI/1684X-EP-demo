#include "../vpp_admission.hpp"
#include <thread>
#include <atomic>
#include <vector>
#include <cassert>
#include <chrono>
int main(){for(unsigned limit: {1u,2u,4u}){vpp_admission::Gate gate(limit);std::atomic<unsigned> active(0),done(0),peak(0);std::vector<std::thread> ts;
for(int i=0;i<16;i++)ts.emplace_back([&]{for(int j=0;j<20;j++){vpp_admission::Lease lease(gate);unsigned n=++active;assert(n<=limit);unsigned p=peak;while(p<n&&!peak.compare_exchange_weak(p,n)){}std::this_thread::sleep_for(std::chrono::microseconds(50));--active;lease.release();lease.release();done++;}});
for(auto&t:ts)t.join();assert(done==320);assert(active==0);assert(peak>0);}}
