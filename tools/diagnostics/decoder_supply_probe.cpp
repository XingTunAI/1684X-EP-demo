// Decode-only diagnostic: no inference, thumbnail, D2H or renderer.
#include <opencv2/opencv.hpp>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using Clock=std::chrono::steady_clock;
using Time=Clock::time_point;
static double secs(Time a,Time b){return std::chrono::duration<double>(b-a).count();}
struct Row { unsigned long decoded=0,measured=0; double fps=0,read_ms=0,max_read_ms=0,max_lag_ms=0,last_lag_ms=0; int error=0; };
int main(int argc,char**argv){
 if(argc!=8){std::cerr<<"input device streams warmup duration paced output\n";return 2;}
 const std::string input=argv[1],output=argv[7];
 int device=std::stoi(argv[2]),n=std::stoi(argv[3]),warm=std::stoi(argv[4]),duration=std::stoi(argv[5]),paced=std::stoi(argv[6]);
 if(n<1||n>32||warm<0||duration<1||(paced!=0&&paced!=1))return 2;
 std::vector<Row> rows(n);std::vector<std::thread> threads;
 std::mutex m;std::condition_variable cv;int ready=0;bool go=false;std::atomic<bool> failed(false);Time start;
 for(int id=0;id<n;++id)threads.emplace_back([&,id](){
  auto&r=rows[id];cv::VideoCapture cap;cv::Mat primed;
  try{
   if(!cap.open(input,cv::CAP_FFMPEG,device)||!cap.set(cv::CAP_PROP_OUTPUT_YUV,1))throw 1;
   r.fps=cap.get(cv::CAP_PROP_FPS);if(r.fps<=0)throw 2;
   if(!cap.read(primed)||primed.empty())throw 3;
  }catch(...){r.error=1;failed=true;}
  {std::unique_lock<std::mutex> lk(m);++ready;cv.notify_all();cv.wait(lk,[&]{return go;});}
  if(failed)return;
  unsigned long sequence=0;
  try{
   for(;;){
    Time due=start+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(sequence/r.fps));
    if(paced)std::this_thread::sleep_until(due);
    Time before=Clock::now();if(secs(start,before)>=warm+duration)break;
    cv::Mat frame;
    if(sequence==0)frame=std::move(primed);
    else if(!cap.read(frame)||frame.empty()){r.error=2;failed=true;break;}
    Time after=Clock::now();++r.decoded;++sequence;
    double elapsed=secs(start,after);
    if(elapsed>=warm&&elapsed<warm+duration){
     ++r.measured;double read=secs(before,after)*1000,lag=std::max(0.,secs(due,after)*1000);
     r.read_ms+=read;r.max_read_ms=std::max(r.max_read_ms,read);r.max_lag_ms=std::max(r.max_lag_ms,lag);r.last_lag_ms=lag;
    }
   }
  }catch(...){r.error=3;failed=true;}
 });
 {std::unique_lock<std::mutex>lk(m);cv.wait(lk,[&]{return ready==n;});start=Clock::now()+std::chrono::milliseconds(100);go=true;cv.notify_all();}
 for(auto&t:threads)t.join();
 std::ofstream f(output);f<<std::setprecision(12)<<"{\"device\":"<<device<<",\"streams\":"<<n<<",\"warmup\":"<<warm<<",\"duration\":"<<duration<<",\"paced\":"<<paced<<",\"failed\":"<<(failed?"true":"false")<<",\"rows\":[";
 unsigned long total=0;
 for(int i=0;i<n;++i){auto&r=rows[i];total+=r.measured;if(i)f<<",";f<<"{\"id\":"<<i<<",\"source_fps\":"<<r.fps<<",\"decoded\":"<<r.decoded<<",\"measured\":"<<r.measured<<",\"decode_fps\":"<<double(r.measured)/duration<<",\"read_mean_ms\":"<<(r.measured?r.read_ms/r.measured:0)<<",\"read_max_ms\":"<<r.max_read_ms<<",\"lag_max_ms\":"<<r.max_lag_ms<<",\"lag_last_ms\":"<<r.last_lag_ms<<",\"error\":"<<r.error<<"}";}
 f<<"],\"decode_fps\":"<<double(total)/duration<<"}\n";f.close();
 std::cout<<"RESULT decode_fps="<<double(total)/duration<<" failed="<<failed<<std::endl;
 return failed?1:0;
}
