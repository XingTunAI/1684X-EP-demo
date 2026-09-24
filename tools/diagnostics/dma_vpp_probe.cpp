#include "bmcv_api_ext.h"
#include "json.hpp"
#include <chrono>
#include <thread>
#include <vector>
#include <memory>
#include <atomic>
#include <fstream>
#include <iostream>
#include <stdexcept>
using Clock=std::chrono::steady_clock;
using json=nlohmann::json;
static void check(bm_status_t r){if(r!=BM_SUCCESS)throw std::runtime_error("SDK error "+std::to_string(r));}
struct Context {
 bm_handle_t h=nullptr;bm_image in{},out{};bool a=false,b=false;
 ~Context(){if(b)bm_image_destroy(out);if(a)bm_image_destroy(in);if(h)bm_dev_free(h);}
 void init(int dev){
  check(bm_dev_request(&h,dev));int si[]={1920,1920},so[]={768};
  check(bm_image_create(h,1080,1920,FORMAT_NV12,DATA_TYPE_EXT_1N_BYTE,&in,si));a=true;
  check(bm_image_create(h,144,256,FORMAT_BGR_PACKED,DATA_TYPE_EXT_1N_BYTE,&out,so));b=true;
  check(bm_image_alloc_dev_mem(in,BMCV_IMAGE_FOR_OUT));check(bm_image_alloc_dev_mem(out,BMCV_IMAGE_FOR_OUT));
  std::vector<unsigned char> y(1920*1080,100),uv(1920*540,128);void* p[]={y.data(),uv.data()};check(bm_image_copy_host_to_device(in,p));
 }
 void run(){int n=1;bmcv_rect_t crop{0,0,1920,1080};check(bmcv_image_vpp_basic(h,1,&in,&out,&n,&crop,nullptr,BMCV_INTER_LINEAR,CSC_YCbCr2RGB_BT709,nullptr));}
};

#include <algorithm>
#include <cmath>
struct Dma {
 bm_handle_t h=nullptr;bm_device_mem_t mem{};bool allocated=false,upload;size_t bytes;
 std::vector<unsigned char> host,reference;
 Dma(int dev,size_t n,bool up):upload(up),bytes(n),host(n),reference(n){
  check(bm_dev_request(&h,dev));
  auto rc=bm_malloc_device_byte(h,&mem,n);if(rc!=BM_SUCCESS){bm_dev_free(h);h=nullptr;check(rc);}allocated=true;
  for(size_t i=0;i<n;i++)reference[i]=(i*17+(up?3:7))%251;
  host=reference;check(bm_memcpy_s2d(h,mem,reference.data()));
 }
 ~Dma(){if(allocated)bm_free_device(h,mem);if(h)bm_dev_free(h);}
 void run(){check(upload?bm_memcpy_s2d(h,mem,host.data()):bm_memcpy_d2s(h,host.data(),mem));}
 void verify(){std::vector<unsigned char> x(bytes);check(bm_memcpy_d2s(h,x.data(),mem));if(x!=reference||host!=reference)throw std::runtime_error("DMA data mismatch");}
};
struct Metric{uint64_t count=0;double sum=0,max=0;void add(double ms){count++;sum+=ms;max=std::max(max,ms);}};
static double mono(Clock::time_point x){return std::chrono::duration<double>(x.time_since_epoch()).count();}
int main(int argc,char**argv){try{
 if(argc!=10)throw std::runtime_error("device bytes workers_per_direction none|upload|download|duplex vpp_threads target_MB_s_per_direction seconds output.json");
 // The final reserved argument records the explicitly selected warmup seconds.
 int dev=std::stoi(argv[1]);size_t bytes=std::stoull(argv[2]);int workers=std::stoi(argv[3]);std::string mode=argv[4];int vn=std::stoi(argv[5]);double rate=std::stod(argv[6]);int seconds=std::stoi(argv[7]);int warmup=std::stoi(argv[9]);
 if(dev<0||bytes<1||bytes>64*1024*1024||workers<0||workers>4||vn<0||vn>32||seconds<1||seconds>120||warmup<0||warmup>30||!std::isfinite(rate)||rate<0||rate>2000||(mode!="none"&&mode!="upload"&&mode!="download"&&mode!="duplex")||(mode=="none"?workers!=0:workers==0)||(mode=="none"&&vn==0))throw std::runtime_error("invalid probe arguments");
 std::vector<std::unique_ptr<Dma>> ds;std::vector<std::unique_ptr<Context>> vs;
 for(bool up:{true,false})if(mode=="duplex"||(up&&mode=="upload")||(!up&&mode=="download"))for(int j=0;j<workers;j++)ds.emplace_back(new Dma(dev,bytes,up));
 for(int j=0;j<vn;j++){vs.emplace_back(new Context);vs.back()->init(dev);vs.back()->run();}
 int dn=ds.size(),total=dn+vn;std::vector<Metric> metrics(total);std::vector<std::thread> threads;
 std::atomic<int> ready(0);std::atomic<bool> go(false),failed(false);Clock::time_point launch,formal,end;
 try{
  for(int i=0;i<total;i++)threads.emplace_back([&,i]{ready++;while(!go.load())std::this_thread::yield();if(failed)return;try{
   std::this_thread::sleep_until(launch);auto next=launch;
   const double period=(i<dn&&rate>0)?double(bytes)*workers/(rate*1e6):0;
   while(!failed&&Clock::now()<end){if(period>0)std::this_thread::sleep_until(next);if(Clock::now()>=end)break;
    auto a=Clock::now();if(i<dn)ds[i]->run();else vs[i-dn]->run();auto b=Clock::now();
    if(a>=formal&&b<=end)metrics[i].add(std::chrono::duration<double,std::milli>(b-a).count());
    if(period>0)next=std::max(next+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(period)),b);
   }
  }catch(...){failed=true;}});
 }catch(...){failed=true;go=true;for(auto&t:threads)t.join();throw;}
 while(ready!=total)std::this_thread::yield();launch=Clock::now()+std::chrono::milliseconds(100);formal=launch+std::chrono::seconds(warmup);end=formal+std::chrono::seconds(seconds);go=true;
 for(auto&t:threads)t.join();if(failed)throw std::runtime_error("probe worker error");
 for(auto&x:ds)x->verify();std::vector<unsigned char> ref;
 for(auto&x:vs){std::vector<unsigned char> pixels(256*144*3);void*p[]={pixels.data()};check(bm_image_copy_device_to_host(x->out,p));if(ref.empty())ref=pixels;else if(ref!=pixels)throw std::runtime_error("VPP outputs differ");for(auto v:pixels)if(v<90||v>105)throw std::runtime_error("invalid VPP gray output");}
 json rows=json::array();Metric up,down,vpp;
 for(int i=0;i<total;i++){auto&m=metrics[i];auto&sum=i>=dn?vpp:(ds[i]->upload?up:down);sum.count+=m.count;sum.sum+=m.sum;sum.max=std::max(sum.max,m.max);
 rows.push_back({{"kind",i>=dn?"vpp":(ds[i]->upload?"upload":"download")},{"count",m.count},{"mean_ms",m.count?m.sum/m.count:0},{"max_ms",m.max}});}
 json result={{"device",dev},{"mode",mode},{"bytes_per_call",bytes},{"workers_per_direction",workers},{"vpp_threads",vn},{"target_MB_s_per_direction",rate},{"seconds",seconds},{"warmup_s",warmup},{"measurement_start_monotonic",mono(formal)},{"measurement_end_monotonic",mono(end)},
 {"upload_MB_s",up.count*double(bytes)/seconds/1e6},{"download_MB_s",down.count*double(bytes)/seconds/1e6},{"upload_mean_ms",up.count?up.sum/up.count:0},{"download_mean_ms",down.count?down.sum/down.count:0},{"vpp_calls_s",double(vpp.count)/seconds},{"vpp_mean_ms",vpp.count?vpp.sum/vpp.count:0},{"data_verified",true},{"threads",rows}};
 std::ofstream f(argv[8]);f<<result.dump(2)<<"\n";if(!f)throw std::runtime_error("output write failed");
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
