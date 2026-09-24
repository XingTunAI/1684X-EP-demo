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
int main(int argc,char**argv){try{
 if(argc!=5)throw std::runtime_error("device threads seconds output.json");
 int dev=std::stoi(argv[1]),n=std::stoi(argv[2]),seconds=std::stoi(argv[3]);if(n<1||n>32||seconds<1)throw std::runtime_error("invalid args");
 std::vector<std::unique_ptr<Context>> cs;for(int i=0;i<n;i++){cs.emplace_back(new Context);cs.back()->init(dev);cs.back()->run();}
 std::atomic<int> ready(0),errors(0);std::atomic<bool> go(false);Clock::time_point start,end;
 struct Metric{uint64_t count=0;double sum=0,max=0;};std::vector<Metric> ms(n);std::vector<std::thread> ts;
 for(int i=0;i<n;i++)ts.emplace_back([&,i](){ready++;while(!go.load())std::this_thread::yield();try{
  while(Clock::now()<end){auto a=Clock::now();cs[i]->run();auto b=Clock::now();if(a>=start&&b<=end){double t=std::chrono::duration<double,std::milli>(b-a).count();ms[i].count++;ms[i].sum+=t;if(t>ms[i].max)ms[i].max=t;}}
 }catch(...){errors++;}});
 while(ready.load()!=n)std::this_thread::yield();start=Clock::now()+std::chrono::seconds(3);end=start+std::chrono::seconds(seconds);go=true;for(auto&t:ts)t.join();if(errors)throw std::runtime_error("worker SDK error");
 json rows=json::array();uint64_t count=0;double sum=0;std::vector<unsigned char> reference;
 for(int i=0;i<n;i++){std::vector<unsigned char> pixels(256*144*3);void*p[]={pixels.data()};check(bm_image_copy_device_to_host(cs[i]->out,p));if(i==0)reference=pixels;else if(pixels!=reference)throw std::runtime_error("thread outputs differ");
 for(auto v:pixels)if(v<90||v>105)throw std::runtime_error("unexpected gray output");
 count+=ms[i].count;sum+=ms[i].sum;rows.push_back({{"count",ms[i].count},{"mean_ms",ms[i].count?ms[i].sum/ms[i].count:0},{"max_ms",ms[i].max}});}
 json result={{"device",dev},{"threads",n},{"seconds",seconds},{"warmup_s",3},{"calls",count},{"calls_per_s",double(count)/seconds},{"mean_ms",count?sum/count:0},{"output_verified",true},{"streams",rows},{"scope","resident NV12 1920x1080 to BGR256x144; no per-call pixel transfers; SDK wall clock"}};
 std::ofstream(argv[4])<<result.dump(2)<<"\n";std::cout<<result.dump()<<"\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
